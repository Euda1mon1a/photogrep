import struct

import pytest

from photogrep.library import LibraryError, decode_lexemes, load_photos, snapshot, term_counts
from photogrep.search import Query, km_between, search


def test_decode_lexemes_ignores_trailing_bytes():
    assert decode_lexemes(struct.pack("<3I", 1, 2, 70000) + b"\x01") == (1, 2, 70000)


def test_snapshot_copies_both_databases(library, tmp_path):
    out = snapshot(library, tmp_path / "snap")
    assert (out / "Photos.sqlite").exists() and (out / "leo.sqlite").exists()


def test_snapshot_rejects_missing_library(tmp_path):
    with pytest.raises(LibraryError):
        snapshot(tmp_path / "nope.photoslibrary", tmp_path / "snap")


def test_snapshot_needs_search_index(library, tmp_path):
    (library / "database" / "search" / "leo.sqlite").unlink()
    with pytest.raises(LibraryError, match="macOS 27"):
        snapshot(library, tmp_path / "snap")


def test_load_photos_joins_terms_faces_and_names(library, tmp_path):
    photos = {p.uuid[0]: p for p in load_photos(snapshot(library, tmp_path / "s"), library)}
    assert set(photos) == {"1", "2", "3"}  # trashed photo excluded
    beach = photos["1"]
    assert beach.labels == ["Beach"] and beach.places == ["Oahu"] and beach.albums == ["Summer Trip"]
    assert (beach.faces, beach.kids, beach.adults) == (7, 1, 6)
    assert beach.filename == "IMG_0001.HEIC" and beach.preview
    assert photos["3"].lat is None  # -180 means no location
    assert photos["2"].words == {"menu"}


@pytest.fixture
def photos(library, tmp_path):
    return load_photos(snapshot(library, tmp_path / "s"), library)


def uuids(hits):
    return [p.uuid[0] for p in hits]


@pytest.mark.parametrize("query, expected", [
    (Query(), ["1", "3", "2"]),                          # ranked by aesthetic score
    (Query(label=["beach"]), ["1"]),                     # case-insensitive
    (Query(label=["grad"]), ["3"]),                      # partial term, events count as labels
    (Query(place=["Francisco"]), ["2"]),
    (Query(person=["Alex"]), ["3"]),
    (Query(album=["summer"]), ["1"]),
    (Query(text=["MENU"]), ["2"]),
    (Query(near=(21.3, -157.85, 5)), ["1"]),
    (Query(since=2024), ["1", "3"]),
    (Query(until=2023), ["2"]),
    (Query(min_adults=6), ["1"]),
    (Query(min_kids=2), ["3"]),
    (Query(max_faces=0), ["2"]),
    (Query(landscape=True), ["1", "3"]),
    (Query(portrait=True), ["2"]),
])
def test_search_filters(photos, query, expected):
    assert uuids(search(photos, query)) == expected


def test_search_top(photos):
    assert uuids(search(photos, Query(), top=1)) == ["1"]


def test_km_between():
    assert km_between((0, 0), (0, 1)) == pytest.approx(111.2, abs=0.2)


def test_term_counts(library, tmp_path):
    counts = term_counts(snapshot(library, tmp_path / "s"), ["scene", "place"])
    assert counts[("scene", "Graduation")] == 1 and counts[("place", "Oahu")] == 1
    assert ("person", "Alex") not in counts


def test_shared_album_previews_are_found(library, tmp_path):
    uuid = "33333333-3333-3333-3333-333333333333"
    src = library / "resources" / "derivatives" / "masters" / "3" / f"{uuid}_4_5005_c.jpeg"
    dest = library / "scopes" / "cloudsharing" / "resources" / "derivatives" / "masters" / "3"
    dest.mkdir(parents=True)
    src.rename(dest / src.name)
    photos = {p.uuid: p for p in load_photos(snapshot(library, tmp_path / "s"), library)}
    assert photos[uuid].preview == str(dest / src.name)
    assert photos[uuid].shared == "shared album"
    assert photos["11111111-1111-1111-1111-111111111111"].shared is None
