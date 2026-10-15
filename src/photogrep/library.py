"""Read-only access to an Apple Photos library on macOS 27.

Everything here works on a snapshot of the two databases Photos keeps (Photos.sqlite and the
search index leo.sqlite), never on the live files.
"""
from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"
CORE_DATA_EPOCH = 978307200  # 2001-01-01 UTC
DATABASES = ("database/Photos.sqlite", "database/search/leo.sqlite")

# leo.sqlite lexicon categories, observed on macOS 27.0.1. Undocumented; Apple may renumber them.
KINDS: dict[str, tuple[int, ...]] = {
    "scene": (4000, 4090),                                       # scene labels, events
    "place": (2060, 2070, 2090, 2100, 2110, 2130, 2140, 2210),   # POI, neighborhood, city, region, state, water
    "person": (3000,),                                           # people you have named
    "album": (7010, 7020),                                       # albums, memories
    "text": (4120,),                                             # words read from the image
}
KID_AGES = {1, 2}  # ZDETECTEDFACE.ZAGETYPE values that look like babies and children (inferred)
# Photos that live outside your own library keep their files under scopes/<name>/
SCOPES = {"cloudsharing": "shared album", "syndication": "shared with you"}


class LibraryError(RuntimeError):
    pass


@dataclass
class Photo:
    uuid: str
    date: str
    year: int
    aesthetic: float
    faces: int
    kids: int
    width: int
    height: int
    lat: float | None
    lon: float | None
    filename: str | None
    labels: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)
    preview: str | None = None
    original: str | None = None
    shared: str | None = None  # "shared album" or "shared with you"; None for your own photos
    words: set[str] = field(default_factory=set, repr=False)

    @property
    def adults(self) -> int:
        return self.faces - self.kids

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "words"}
        d["adults"] = self.adults
        return d


def snapshot(library: Path, dest: Path) -> Path:
    """Clone Photos.sqlite and leo.sqlite (with their WAL files) into dest.

    On APFS `cp -c` makes a copy-on-write clone: instant, and it takes no disk space until
    Photos changes the original. Other file systems fall back to a full copy.
    """
    if not (library / DATABASES[0]).exists():
        raise LibraryError(f"no Photos library at {library}")
    if not (library / DATABASES[1]).exists():
        raise LibraryError(f"no search index at {library / DATABASES[1]}; photogrep needs macOS 27 or later")
    dest.mkdir(parents=True, exist_ok=True)
    for rel in DATABASES:
        for suffix in ("", "-wal", "-shm"):
            src = library / f"{rel}{suffix}"
            if not src.exists():
                continue
            out = dest / src.name
            if subprocess.run(["cp", "-c", str(src), str(out)], capture_output=True).returncode == 0:
                continue
            try:
                shutil.copy2(src, out)
            except PermissionError as e:
                raise LibraryError(f"cannot read {src}; give your terminal Full Disk Access "
                                   "(System Settings > Privacy & Security)") from e
    return dest


def decode_lexemes(blob: bytes) -> tuple[int, ...]:
    """items.lexeme_ids is a little-endian uint32 array."""
    n = len(blob) // 4
    return struct.unpack(f"<{n}I", blob[: n * 4])


def load_index(db_dir: Path) -> dict[str, dict[int, set[str]]]:
    """Asset UUID -> lexicon category -> terms."""
    leo = sqlite3.connect(db_dir / "leo.sqlite")
    lexicon: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for lid, cat, content in leo.execute("select lexeme_id, category, content from lexicon"):
        if content:
            lexicon[lid].append((cat, content))
    index: dict[str, dict[int, set[str]]] = {}
    for uuid, blob in leo.execute("select identifier, lexeme_ids from items where type = 1"):
        if not blob:
            continue
        by_cat: dict[int, set[str]] = defaultdict(set)
        for lid in decode_lexemes(blob):
            for cat, content in lexicon.get(lid, ()):
                by_cat[cat].add(content)
        index[uuid] = by_cat
    leo.close()
    return index


def term_counts(db_dir: Path, kinds: list[str]) -> Counter:
    """How many photos carry each term, for the given kinds."""
    cats = {c: k for k in kinds for c in KINDS[k]}
    leo = sqlite3.connect(db_dir / "leo.sqlite")
    names = {lid: (cats[cat], content) for lid, cat, content
             in leo.execute("select lexeme_id, category, content from lexicon") if cat in cats and content}
    counts: Counter = Counter()
    for (blob,) in leo.execute("select lexeme_ids from items where type = 1"):
        if blob:
            counts.update({names[lid] for lid in decode_lexemes(blob) if lid in names})
    leo.close()
    return counts


def _files(library: Path, uuid: str, directory: str, filename: str,
           scopes: list[str]) -> tuple[str | None, str | None, str | None]:
    """Preview, original (when downloaded) and sharing scope for one asset."""
    original = library / "originals" / directory / filename
    preview = library / "resources" / "derivatives" / "masters" / directory / f"{uuid}_4_5005_c.jpeg"
    if preview.exists():
        return str(preview), (str(original) if original.exists() else None), None
    larger = [library / "resources" / "derivatives" / directory / f"{uuid}{s}"
              for s in ("_1_201_a.jpeg", "_1_102_o.jpeg", "_1_105_c.jpeg")]
    found = next((p for p in larger if p.exists()), None)
    if found:
        return str(found), (str(original) if original.exists() else None), None
    for scope in scopes:
        p = library / "scopes" / scope / "resources" / "derivatives" / "masters" / uuid[0] / f"{uuid}_4_5005_c.jpeg"
        if p.exists():
            return str(p), None, SCOPES.get(scope, "shared")
    return None, None, None


def load_photos(db_dir: Path, library: Path) -> list[Photo]:
    """Every visible, untrashed still photo with its labels, places, people, albums and faces."""
    index = load_index(db_dir)
    db = sqlite3.connect(db_dir / "Photos.sqlite")
    faces: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for asset, age in db.execute("select ZASSETFORFACE, ZAGETYPE from ZDETECTEDFACE "
                                 "where ZASSETFORFACE is not null and coalesce(ZHIDDEN, 0) = 0"):
        faces[asset][0] += 1
        faces[asset][1] += age in KID_AGES
    rows = db.execute("""
        select a.Z_PK, a.ZUUID, a.ZDIRECTORY, a.ZFILENAME, a.ZOVERALLAESTHETICSCORE, a.ZWIDTH, a.ZHEIGHT,
               a.ZLATITUDE, a.ZLONGITUDE, a.ZDATECREATED, x.ZORIGINALFILENAME, x.ZTIMEZONEOFFSET
        from ZASSET a left join ZADDITIONALASSETATTRIBUTES x on x.ZASSET = a.Z_PK
        where a.ZTRASHEDSTATE = 0 and a.ZHIDDEN = 0 and a.ZKIND = 0""").fetchall()
    db.close()
    scope_root = library / "scopes"
    scopes = sorted(d.name for d in scope_root.iterdir() if d.is_dir()) if scope_root.is_dir() else []
    photos = []
    for pk, uuid, directory, filename, aes, w, h, lat, lon, created, original_name, tz in rows:
        cats = index.get(uuid, {})
        terms = {k: set().union(*(cats.get(c, set()) for c in v)) for k, v in KINDS.items()}
        n, kids = faces.get(pk, (0, 0))
        # Photos shows the date where the photo was taken, so apply its recorded UTC offset
        when = datetime.fromtimestamp(CORE_DATA_EPOCH + (created or 0), tz=timezone.utc) + timedelta(seconds=tz or 0)
        has_gps = lat is not None and lon is not None and lat != -180.0
        preview, original, shared = _files(library, uuid, str(directory), str(filename), scopes)
        photos.append(Photo(
            uuid=uuid, date=when.date().isoformat(), year=when.year, aesthetic=round(aes or 0, 3),
            faces=n, kids=kids, width=w or 0, height=h or 0,
            lat=lat if has_gps else None, lon=lon if has_gps else None, filename=original_name,
            labels=sorted(terms["scene"]), places=sorted(terms["place"]), people=sorted(terms["person"]),
            albums=sorted(terms["album"]), preview=preview, original=original, shared=shared,
            words=terms["text"]))
    return photos
