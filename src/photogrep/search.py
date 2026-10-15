"""Filter photos by what is in them, and rank by Apple's aesthetic score."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .library import Photo


@dataclass
class Query:
    label: list[str] | None = None
    place: list[str] | None = None
    person: list[str] | None = None
    album: list[str] | None = None
    text: list[str] | None = None
    near: tuple[float, float, float] | None = None  # lat, lon, km
    since: int | None = None
    until: int | None = None
    min_faces: int = 0
    max_faces: int | None = None
    min_adults: int = 0
    min_kids: int = 0
    max_kids: int | None = None
    landscape: bool = False
    portrait: bool = False


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def matches_any(wanted: list[str], have: list[str] | set[str]) -> bool:
    """Case-insensitive; a wanted term matches a whole term or part of one ("Grad" finds "Graduation")."""
    low = [h.lower() for h in have]
    return any(w.lower() in h for w in wanted for h in low)


def keep(p: Photo, q: Query) -> bool:
    if q.label and not matches_any(q.label, p.labels):
        return False
    if q.place and not matches_any(q.place, p.places):
        return False
    if q.person and not matches_any(q.person, p.people):
        return False
    if q.album and not matches_any(q.album, p.albums):
        return False
    if q.text:
        words = {w.lower() for w in p.words}
        if not all(t.lower() in words for t in q.text):
            return False
    if q.near and (p.lat is None or km_between((p.lat, p.lon), q.near[:2]) > q.near[2]):
        return False
    if (q.since and p.year < q.since) or (q.until and p.year > q.until):
        return False
    if p.faces < q.min_faces or (q.max_faces is not None and p.faces > q.max_faces):
        return False
    if p.adults < q.min_adults or p.kids < q.min_kids or (q.max_kids is not None and p.kids > q.max_kids):
        return False
    if q.landscape and not p.width > p.height * 1.15:
        return False
    if q.portrait and not p.height > p.width * 1.15:
        return False
    return True


def search(photos: list[Photo], q: Query, top: int | None = None) -> list[Photo]:
    hits = sorted((p for p in photos if keep(p, q)), key=lambda p: p.aesthetic, reverse=True)
    return hits[:top] if top else hits
