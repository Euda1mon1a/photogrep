import numpy as np
import pytest
from PIL import Image

from photogrep import match


def brute_ncc(image, query):
    th, tw = query.shape
    t0 = query - query.mean()
    out = np.full((image.shape[0] - th + 1, image.shape[1] - tw + 1), -1.0)
    for y in range(out.shape[0]):
        for x in range(out.shape[1]):
            w = image[y:y + th, x:x + tw]
            w0 = w - w.mean()
            d = np.sqrt((w0 * w0).sum() * (t0 * t0).sum())
            out[y, x] = (w0 * t0).sum() / d
    return out


def test_fft_ncc_equals_brute_force():
    rng = np.random.default_rng(0)
    image, query = rng.random((30, 40)) * 255, rng.random((8, 12)) * 255
    np.testing.assert_allclose(match.ncc_map(image, query), brute_ncc(image, query), atol=1e-9)


def test_flat_windows_score_minus_one():
    image = np.zeros((20, 20))
    image[:, 10:] = np.random.default_rng(1).random((20, 10)) * 255
    scores = match.ncc_map(image, np.random.default_rng(2).random((5, 5)) * 255)
    assert scores[0, 0] == -1.0


def crop(path, box, width, tmp_path, name):
    im = Image.open(path)
    w, h = im.size
    c = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    c = c.resize((width, round(width * c.height / c.width)))
    out = tmp_path / name
    c.save(out, quality=55)
    return out


@pytest.mark.parametrize("box", [(0, 0, 1, 1), (0.1, 0.2, 0.9, 0.7), (0.5, 0.4, 0.95, 0.9)])
def test_finds_the_source_of_a_crop(preview, tmp_path, box):
    source = preview("11111111-1111-1111-1111-111111111111")
    other = preview("33333333-3333-3333-3333-333333333333")
    query = match.load_query(crop(source, box, 300, tmp_path, "q.jpg"))
    best, compared, failed = match.rank([query], [("src", str(source)), ("other", str(other))], top=2, workers=1)
    assert compared == 2 and not failed
    assert best[0][0].key == "src" and best[0][0].score >= match.MATCH
    assert best[0][1].score < match.POSSIBLE


def test_unreadable_candidates_are_reported_not_scored(preview, tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    query = match.load_query(preview("11111111-1111-1111-1111-111111111111"))
    best, compared, failed = match.rank([query], [("bad", str(bad))], workers=1)
    assert compared == 0 and failed == ["bad"] and best == [[]]


def test_uniform_query_is_refused(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("L", (100, 60), 128).save(flat)
    with pytest.raises(ValueError, match="too little detail"):
        match.load_query(flat)


def test_verdicts():
    assert [match.verdict(s) for s in (0.95, 0.85, 0.5)] == ["match", "possible", "no match"]
