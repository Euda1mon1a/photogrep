"""Find the photo an image was cut from, even after cropping, resizing or recompression.

Two passes. The coarse pass shrinks the query to a 64-pixel-wide grayscale thumbnail and
slides it across every candidate at seven scales, keeping the best normalized
cross-correlation (NCC) and the scale it came from. The fine pass re-checks the 25
strongest candidates at 160 pixels across a narrow band of scales around that one, twice:
on the plain image, and on its edges (the image minus a blurred copy). Large shapes and
smooth gradients make unrelated photos look alike to plain NCC; edges only line up on the
true source. The score is min(plain, edges + 0.15).

Validated on 198 random crops of real library photos (42-100% of the width, resized,
recompressed) looked up among 598 photos: the source ranked first every time and 192
scored 0.90 or higher; no other photo reached 0.90 (the highest was 0.83).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageFilter, ImageOps

COARSE_WIDTH = 64
FINE_WIDTH = 160
SMALLEST = 0.4  # the query must cover at least this share of the candidate's width
COARSE_STEP = 1.15
FINE_STEPS = np.linspace(0.88, 1.12, 13)
SHORTLIST = 25
COARSE_DRAFT = round(COARSE_WIDTH / SMALLEST)  # the largest thumbnail the coarse pass needs
EDGE_BONUS = 0.15
MIN_DETAIL = 6.0  # edge contrast below this is a blank wall or sky: too little to match on
MATCH = 0.90
POSSIBLE = 0.80
# Photos reads the library through TCC; more than four concurrent readers can deadlock tccd.
MAX_WORKERS = 4
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".gif", ".bmp"}

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC support is optional
    pass


def _scales() -> tuple[float, ...]:
    out, f = [], 1.0
    while f >= SMALLEST - 1e-9:
        out.append(f)
        f /= COARSE_STEP
    return tuple(out)


COARSE_SCALES = _scales()


@dataclass
class Query:
    coarse: np.ndarray
    fine: np.ndarray
    edges: np.ndarray


@dataclass
class Hit:
    score: float
    key: str  # a library UUID or a file path


def open_gray(path: str | Path, draft: int | None = 320) -> Image.Image:
    im = Image.open(path)
    if draft and im.format == "JPEG":
        im.draft("L", (draft, draft))  # decode at reduced size; much faster for large JPEGs
    return ImageOps.exif_transpose(im).convert("L")


def thumb(im: Image.Image, width: int) -> np.ndarray:
    height = max(1, round(im.height * width / im.width))
    return np.asarray(im.resize((width, height), Image.BILINEAR), dtype=np.float32)


def edges(a: np.ndarray) -> np.ndarray:
    """The image minus a blurred copy, scaled so flat-window rejection in ncc_map still applies."""
    blurred = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    return (a - np.asarray(blurred, dtype=np.float32)) * 4


def load_query(path: str | Path) -> Query:
    im = open_gray(path, draft=None)
    coarse = thumb(im, COARSE_WIDTH)
    fine = thumb(im, min(FINE_WIDTH, max(COARSE_WIDTH, im.width)))
    e = edges(fine)
    if coarse.std() < 2 or e.std() < MIN_DETAIL:
        raise ValueError(f"{path}: too little detail to match reliably")
    return Query(coarse, fine, e)


def window_sums(a: np.ndarray, h: int, w: int) -> np.ndarray:
    c = np.pad(a.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def _fast_len(n: int) -> int:
    """Smallest 2^a 3^b 5^c >= n; FFTs of these sizes are several times faster than of primes."""
    best = 2 * n
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p235 = p35
            while p235 < n:
                p235 *= 2
            best = min(best, p235)
            p35 *= 3
        p5 *= 5
    return best


def ncc_map(image: np.ndarray, query: np.ndarray, kernels: dict | None = None) -> np.ndarray:
    """Normalized cross-correlation of query at every position inside image (FFT, valid region).

    kernels caches the query's transform by FFT size; pass a dict only for a fixed query.
    """
    th, tw = query.shape
    H, W = image.shape
    t0 = query - query.mean()
    shape = (_fast_len(H + th - 1), _fast_len(W + tw - 1))
    kernel = kernels.get(shape) if kernels is not None else None
    if kernel is None:
        kernel = np.fft.rfft2(t0[::-1, ::-1], shape)
        if kernels is not None:
            kernels[shape] = kernel
    conv = np.fft.irfft2(np.fft.rfft2(image, shape) * kernel, shape)
    numerator = conv[th - 1:H, tw - 1:W]
    n = th * tw
    s1, s2 = window_sums(image, th, tw), window_sums(image * image, th, tw)
    spread = s2 - s1 * s1 / n  # n * variance of each window
    out = numerator / (np.sqrt(np.maximum(spread, 1e-9)) * np.sqrt(float((t0 * t0).sum())))
    out[spread < 4 * n] = -1.0  # flat windows (std < 2 levels) carry no evidence
    return out


def fit(query: np.ndarray, image: np.ndarray) -> np.ndarray | None:
    """Trim a query that overhangs the image by rounding (up to 6%); None if it does not fit."""
    th, tw = query.shape
    H, W = image.shape
    if th > H:
        if th - H > max(2, 0.06 * th):
            return None
        cut = th - H
        query = query[cut // 2: th - (cut - cut // 2)]
    if tw > W:
        if tw - W > max(2, 0.06 * tw):
            return None
        cut = tw - W
        query = query[:, cut // 2: tw - (cut - cut // 2)]
    return query


def best_over(candidate: Image.Image, query: np.ndarray, scales: Iterable[float],
              kernels: dict | None = None) -> tuple[float, float]:
    """Best correlation of query inside candidate over the given scales, and the scale it came from."""
    best, best_scale = -1.0, 1.0
    tw = query.shape[1]
    for s in scales:
        if s > 1.0 + 1e-9:
            continue
        image = thumb(candidate, round(tw / s))
        q = fit(query, image)
        if q is None:
            continue
        score = float(ncc_map(image, q, kernels if q is query else None).max())
        if score > best:
            best, best_scale = score, s
    return best, best_scale


_QUERIES: list[Query] = []
_KERNELS: list[dict] = []


def _init(queries: list[Query]) -> None:
    global _QUERIES, _KERNELS
    _QUERIES = queries
    _KERNELS = [{} for _ in queries]


def _coarse_chunk(chunk: list[tuple[str, str]]) -> list[tuple[str, list[tuple[float, float]] | None]]:
    out = []
    for key, path in chunk:
        try:
            im = open_gray(path, draft=COARSE_DRAFT)
            out.append((key, [best_over(im, q.coarse, COARSE_SCALES, k) for q, k in zip(_QUERIES, _KERNELS)]))
        except Exception:
            out.append((key, None))  # unreadable: reported as not compared, never as "no match"
    return out


def fine_score(path: str, query: Query, scale: float) -> float:
    im = open_gray(path, draft=640)
    plain = edge = -1.0
    tw = query.fine.shape[1]
    for k in FINE_STEPS:
        image = thumb(im, round(tw / min(1.0, scale * k)))
        q = fit(query.fine, image)
        if q is None:
            continue
        plain = max(plain, float(ncc_map(image, q).max()))
        edge = max(edge, float(ncc_map(edges(image), fit(query.edges, image)).max()))
    return min(plain, edge + EDGE_BONUS)


def rank(queries: list[Query], candidates: list[tuple[str, str]], top: int = 5,
         workers: int = MAX_WORKERS, progress: Callable[[int, int], None] | None = None,
         ) -> tuple[list[list[Hit]], int, list[str]]:
    """Score every (key, image path) candidate against every query.

    Returns the top hits per query (fine-pass scores), how many candidates were compared,
    and the keys that could not be read.
    """
    keep = max(top, SHORTLIST)
    shortlist: list[list[tuple[float, float, str]]] = [[] for _ in queries]
    failed: list[str] = []
    compared = 0
    size = 400
    chunks = [candidates[i:i + size] for i in range(0, len(candidates), size)]
    workers = max(1, min(workers, MAX_WORKERS))
    paths = dict(candidates)

    def absorb(results: list[tuple[str, list[tuple[float, float]] | None]]) -> None:
        nonlocal compared
        for key, scores in results:
            if scores is None:
                failed.append(key)
                continue
            compared += 1
            for i, (score, scale) in enumerate(scores):
                shortlist[i].append((score, scale, key))
        for i in range(len(shortlist)):
            shortlist[i] = sorted(shortlist[i], reverse=True)[:keep]

    if workers == 1:
        _init(queries)
        for k, chunk in enumerate(chunks):
            absorb(_coarse_chunk(chunk))
            if progress:
                progress(min((k + 1) * size, len(candidates)), len(candidates))
    else:
        done = 0
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(queries,)) as pool:
            futures = {pool.submit(_coarse_chunk, c): len(c) for c in chunks}
            for f in as_completed(futures):
                absorb(f.result())
                done += futures[f]
                if progress:
                    progress(done, len(candidates))

    best: list[list[Hit]] = []
    for q, short in zip(queries, shortlist):
        hits = []
        for _, scale, key in short:
            try:
                hits.append(Hit(round(fine_score(paths[key], q, scale), 4), key))
            except Exception:
                continue
        best.append(sorted(hits, key=lambda h: h.score, reverse=True)[:top])
    return best, compared, failed


def image_files(folders: Iterable[Path]) -> list[Path]:
    files = []
    for folder in folders:
        if folder.is_file():
            files.append(folder)
            continue
        for root, _, names in os.walk(folder):
            files.extend(Path(root) / n for n in names if Path(n).suffix.lower() in IMAGE_SUFFIXES)
    return sorted(files)


def verdict(score: float) -> str:
    return "match" if score >= MATCH else "possible" if score >= POSSIBLE else "no match"
