import json

import pytest
from PIL import Image

from photogrep.cli import main


def run(capsys, *argv):
    with pytest.raises(SystemExit) as exit_:
        main(list(argv))
    out = capsys.readouterr()
    return exit_.value.code, out.out, out.err


def test_search_json(library, capsys):
    code, out, _ = run(capsys, "search", "--library", str(library), "--label", "Beach", "--json")
    rows = json.loads(out)
    assert code == 0 and [r["filename"] for r in rows] == ["IMG_0001.HEIC"]
    assert rows[0]["adults"] == 6 and "words" not in rows[0]


def test_search_sheet(library, tmp_path, capsys):
    sheet = tmp_path / "sheet.jpg"
    code, out, _ = run(capsys, "search", "--library", str(library), "--sheet", str(sheet))
    assert code == 0 and Image.open(sheet).size[0] > 0 and len(out.splitlines()) == 3


def test_match_library(library, preview, tmp_path, capsys):
    q = tmp_path / "q.jpg"
    Image.open(preview("22222222-2222-2222-2222-222222222222")).crop((40, 60, 320, 300)).save(q)
    code, out, _ = run(capsys, "match", str(q), "--library", str(library), "--workers", "1", "--json")
    report = json.loads(out)[0]
    assert code == 0 and report["verdict"] == "match"
    assert report["candidates"][0]["filename"] == "IMG_0002.HEIC"


def test_match_reports_no_match_with_exit_1(library, tmp_path, capsys):
    from conftest import texture
    q = tmp_path / "unrelated.jpg"
    texture(1234).save(q)
    code, out, _ = run(capsys, "match", str(q), "--library", str(library), "--workers", "1")
    assert code == 1 and "no match among 3 library photos" in out


def test_match_folder_only(preview, tmp_path, capsys):
    folder = tmp_path / "folder"
    folder.mkdir()
    src = Image.open(preview("11111111-1111-1111-1111-111111111111"))
    src.save(folder / "copy.png")
    src.resize((200, 150)).save(tmp_path / "small.jpg")
    code, out, _ = run(capsys, "match", str(tmp_path / "small.jpg"), "--no-library", "--in", str(folder),
                       "--workers", "1")
    assert code == 0 and "copy.png" in out


def test_labels(library, capsys):
    code, out, _ = run(capsys, "labels", "--library", str(library), "--like", "grad")
    assert code == 0 and out.split() == ["1", "scene", "Graduation"]


def test_bad_near_is_an_error(library, capsys):
    code, _, err = run(capsys, "search", "--library", str(library), "--near", "21.3")
    assert code == 2 and "LAT,LON,KM" in err


def test_missing_library_is_an_error(tmp_path, capsys):
    code, _, err = run(capsys, "search", "--library", str(tmp_path / "none.photoslibrary"))
    assert code == 2 and "no Photos library" in err


def test_search_nothing_found_exits_1(library, capsys):
    code, out, err = run(capsys, "search", "--library", str(library), "--label", "Volcano")
    assert code == 1 and out == "" and err.strip() == "0 photos"


def test_search_reports_total_when_limited(library, capsys):
    code, out, err = run(capsys, "search", "--library", str(library), "--top", "1")
    assert code == 0 and len(out.splitlines()) == 1 and err.strip() == "1 of 3 photos"


def test_labels_json(library, capsys):
    code, out, _ = run(capsys, "labels", "--library", str(library), "--kind", "place", "--json")
    assert code == 0 and {r["term"] for r in json.loads(out)} == {"Oahu", "San Francisco"}


def test_labels_nothing_found_exits_1(library, capsys):
    code, out, _ = run(capsys, "labels", "--library", str(library), "--like", "zzz")
    assert code == 1 and out == ""


def test_match_filters_narrow_the_pool(library, preview, tmp_path, capsys):
    q = tmp_path / "q.jpg"
    Image.open(preview("22222222-2222-2222-2222-222222222222")).crop((40, 60, 320, 300)).save(q)
    code, out, _ = run(capsys, "match", str(q), "--library", str(library), "--workers", "1", "--since", "2024")
    assert code == 1 and "no match among 2 library photos matching the filters" in out
    code, out, _ = run(capsys, "match", str(q), "--library", str(library), "--workers", "1", "--place", "Francisco")
    assert code == 0 and "IMG_0002.HEIC" in out


def test_unreadable_query_is_an_error(tmp_path, capsys):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    code, _, err = run(capsys, "match", str(bad), "--no-library", "--in", str(tmp_path))
    assert code == 2 and "photogrep:" in err
