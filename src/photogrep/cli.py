"""photogrep command line."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import __version__, match, photos_app
from .library import DEFAULT_LIBRARY, KINDS, LibraryError, Photo, load_photos, snapshot, term_counts
from .search import Query, search
from .sheet import contact_sheet

EXAMPLES = """examples:
  photogrep search --label Beach --label Sunset --place Maui --max-faces 0 --landscape
  photogrep search --near 37.7694,-122.4862,1 --min-adults 6 --since 2024 --sheet groups.jpg
  photogrep search --text menu --album Travel --json
  photogrep match screenshot.png
  photogrep match old-slide-photo.jpg --in ~/Downloads
  photogrep labels --like grad
  photogrep export 1A2B3C4D-0000-0000-0000-000000000000 --to ~/Desktop/picks
  photogrep show 1A2B3C4D-0000-0000-0000-000000000000
"""


@contextmanager
def database(args: argparse.Namespace) -> Iterator[Path]:
    if args.db_dir:
        yield args.db_dir
        return
    with tempfile.TemporaryDirectory(prefix="photogrep-") as tmp:
        yield snapshot(args.library, Path(tmp))


def progress(done: int, total: int) -> None:
    if sys.stderr.isatty():
        end = "\n" if done >= total else ""
        print(f"\rcompared {done:,} of {total:,}", end=end, file=sys.stderr, flush=True)


def describe(p: Photo) -> str:
    where = ", ".join(p.places[:2])
    labels = ", ".join(p.labels[:4])
    return (f"{p.date}  aes {p.aesthetic:.3f}  faces {p.faces:2d} (kids {p.kids})  {p.width}x{p.height}  "
            f"{p.filename or '-'}  {p.uuid}  {where}  [{labels}]")


def cmd_search(args: argparse.Namespace) -> int:
    near = None
    if args.near:
        try:
            lat, lon, km = (float(x) for x in args.near.split(","))
        except ValueError:
            raise SystemExit("photogrep: --near takes LAT,LON,KM (for example 37.7694,-122.4862,1)")
        near = (lat, lon, km)
    q = Query(label=args.label, place=args.place, person=args.person, album=args.album, text=args.text,
              near=near, since=args.since, until=args.until, min_faces=args.min_faces,
              max_faces=args.max_faces, min_adults=args.min_adults, min_kids=args.min_kids,
              max_kids=args.max_kids, landscape=args.landscape, portrait=args.portrait)
    with database(args) as db:
        hits = search(load_photos(db, args.library), q, args.top)
    if args.json:
        print(json.dumps([p.to_dict() for p in hits], indent=1, ensure_ascii=False))
    else:
        for k, p in enumerate(hits, start=1):
            print(f"{k:3d}  {describe(p)}")
        print(f"{len(hits)} photo{'s' if len(hits) != 1 else ''}", file=sys.stderr)
    if args.sheet and hits:
        contact_sheet(hits, args.sheet)
        print(f"contact sheet: {args.sheet}", file=sys.stderr)
    if args.export and hits:
        folders = photos_app.export([p.uuid for p in hits], args.export, args.originals)
        print(f"exported {len(folders)} to {args.export}", file=sys.stderr)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    queries = []
    for path in args.images:
        try:
            queries.append(match.load_query(path))
        except (OSError, ValueError) as e:
            raise SystemExit(f"photogrep: {e}")
    candidates: list[tuple[str, str]] = []
    photos: dict[str, Photo] = {}
    no_preview = 0
    if not args.no_library:
        with database(args) as db:
            for p in load_photos(db, args.library):
                photos[p.uuid] = p
                if p.preview:
                    candidates.append((p.uuid, p.preview))
                else:
                    no_preview += 1
    query_paths = {str(Path(i).resolve()) for i in args.images}
    files = [f for f in match.image_files(args.within) if str(f.resolve()) not in query_paths]
    candidates += [(str(f), str(f)) for f in files]
    if not candidates:
        raise SystemExit("photogrep: nothing to compare against")
    best, compared, failed = match.rank(queries, candidates, top=max(args.top, 1), workers=args.workers,
                                        progress=progress)
    unreadable = no_preview + len(failed)
    report = []
    for image, hits in zip(args.images, best):
        top = hits[0].score if hits else -1.0
        report.append({
            "image": str(image), "verdict": match.verdict(top), "compared": compared, "not_compared": unreadable,
            "candidates": [{"score": h.score, "verdict": match.verdict(h.score),
                            **(photos[h.key].to_dict() if h.key in photos else {"path": h.key})} for h in hits]})
    if args.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0 if all(r["verdict"] == "match" for r in report) else 1
    pool = ([f"{len(photos):,} library photos"] if not args.no_library else []) + \
           ([f"{len(files):,} files"] if args.within else [])
    for r in report:
        print(Path(r["image"]).name)
        if r["verdict"] == "no match":
            closest = r["candidates"][0]["score"] if r["candidates"] else 0
            print(f"  no match among {' and '.join(pool)} (closest {closest:.2f}; "
                  f"a match scores {match.MATCH:.2f} or higher)")
        shown = r["candidates"] if r["verdict"] != "no match" or args.top > 1 else []
        for c in shown:
            if "uuid" in c:
                where = ", ".join(c["places"][:2] + ([f"({c['shared']})"] if c["shared"] else []))
                print(f"  {c['verdict']:8s} {c['score']:.2f}  {c['date']}  {c['filename'] or '-'}  {where}")
                print(f"           {c['uuid']}")
            else:
                print(f"  {c['verdict']:8s} {c['score']:.2f}  {c['path']}")
        if unreadable:
            print(f"  not compared: {unreadable:,} without a readable preview")
    return 0 if all(r["verdict"] == "match" for r in report) else 1


def cmd_labels(args: argparse.Namespace) -> int:
    kinds = args.kind or list(KINDS)
    with database(args) as db:
        counts = term_counts(db, kinds)
    rows = [(n, kind, term) for (kind, term), n in counts.items()
            if not args.like or args.like.lower() in term.lower()]
    rows.sort(key=lambda r: (-r[0], r[2]))
    for n, kind, term in rows[: args.top]:
        print(f"{n:7,d}  {kind:6s}  {term}")
    return 0


def resolve_uuid(prefix: str, args: argparse.Namespace) -> str:
    if photos_app.UUID.match(prefix):
        return prefix
    with database(args) as db:
        found = [p.uuid for p in load_photos(db, args.library) if p.uuid.lower().startswith(prefix.lower())]
    if len(found) != 1:
        raise SystemExit(f"photogrep: {prefix} matches {len(found)} photos; give more of the UUID")
    return found[0]


def cmd_export(args: argparse.Namespace) -> int:
    uuids = [resolve_uuid(u, args) for u in args.uuids]
    for folder in photos_app.export(uuids, args.to, args.originals):
        for f in sorted(folder.iterdir()):
            print(f)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    photos_app.show(resolve_uuid(args.uuid, args))
    return 0


def parser() -> argparse.ArgumentParser:
    lib = argparse.ArgumentParser(add_help=False)
    lib.add_argument("--library", type=Path, default=DEFAULT_LIBRARY,
                     help="Photos library (default: ~/Pictures/Photos Library.photoslibrary)")
    lib.add_argument("--db-dir", type=Path, help="reuse a folder holding Photos.sqlite and leo.sqlite copies")

    ap = argparse.ArgumentParser(prog="photogrep", epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description="Search Apple Photos by what is in the picture, "
                                             "and find where an image came from.")
    ap.add_argument("--version", action="version", version=f"photogrep {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", parents=[lib], help="find photos by labels, places, people, faces and more")
    s.add_argument("--label", action="append", help="scene or event label, any of (Beach, Hike, Party)")
    s.add_argument("--place", action="append", help="place name, any of (Paris, Yosemite)")
    s.add_argument("--person", action="append", help="person you have named in Photos, any of")
    s.add_argument("--album", action="append", help="album or memory title, any of")
    s.add_argument("--text", action="append", help="word read from the image; all must appear")
    s.add_argument("--near", metavar="LAT,LON,KM", help="taken within KM of a point")
    s.add_argument("--since", type=int, metavar="YEAR", help="taken in or after YEAR")
    s.add_argument("--until", type=int, metavar="YEAR", help="taken in or before YEAR")
    s.add_argument("--min-faces", type=int, default=0)
    s.add_argument("--max-faces", type=int)
    s.add_argument("--min-adults", type=int, default=0)
    s.add_argument("--min-kids", type=int, default=0)
    s.add_argument("--max-kids", type=int)
    s.add_argument("--landscape", action="store_true")
    s.add_argument("--portrait", action="store_true")
    s.add_argument("--top", type=int, default=20, help="how many to show, best first (default 20; 0 for all)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--sheet", type=Path, metavar="FILE.jpg", help="also write a contact sheet")
    s.add_argument("--export", type=Path, metavar="DIR", help="also export full-resolution files through Photos")
    s.add_argument("--originals", action="store_true", help="export unedited originals")
    s.set_defaults(func=cmd_search)

    m = sub.add_parser("match", parents=[lib], help="find which photo an image was cut from")
    m.add_argument("images", nargs="+", type=Path, help="image files to look up (crops and resizes are fine)")
    m.add_argument("--in", dest="within", action="append", type=Path, default=[], metavar="DIR",
                   help="also search image files in this folder (repeatable)")
    m.add_argument("--no-library", action="store_true", help="search only the --in folders")
    m.add_argument("--top", type=int, default=1, help="candidates to show per image (default 1)")
    m.add_argument("--workers", type=int, default=match.MAX_WORKERS,
                   help=f"parallel processes (at most {match.MAX_WORKERS})")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_match)

    lb = sub.add_parser("labels", parents=[lib], help="list the terms Photos has indexed, most common first")
    lb.add_argument("--kind", action="append", choices=list(KINDS))
    lb.add_argument("--like", help="only terms containing this text")
    lb.add_argument("--top", type=int, default=50)
    lb.set_defaults(func=cmd_labels)

    e = sub.add_parser("export", parents=[lib], help="export full-resolution files through Photos")
    e.add_argument("uuids", nargs="+", metavar="UUID", help="photo UUID (a unique prefix is enough)")
    e.add_argument("--to", type=Path, required=True, metavar="DIR")
    e.add_argument("--originals", action="store_true", help="export unedited originals")
    e.set_defaults(func=cmd_export)

    sh = sub.add_parser("show", parents=[lib], help="open Photos with this photo selected")
    sh.add_argument("uuid", metavar="UUID", help="photo UUID (a unique prefix is enough)")
    sh.set_defaults(func=cmd_show)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        sys.exit(args.func(args))
    except LibraryError as e:
        raise SystemExit(f"photogrep: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
