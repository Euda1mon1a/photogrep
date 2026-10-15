"""A tiny synthetic Photos library with the tables and files photogrep reads."""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

# uuid, aesthetic, (w, h), (lat, lon), year, faces [age types], terms [(category, term)], original name
ASSETS = [
    ("11111111-1111-1111-1111-111111111111", 0.9, (4032, 3024), (21.30, -157.85), 2025, [5, 5, 5, 5, 5, 5, 1],
     [(4000, "Beach"), (2110, "Oahu"), (7010, "Summer Trip")], "IMG_0001.HEIC"),
    ("22222222-2222-2222-2222-222222222222", 0.7, (3024, 4032), (37.77, -122.49), 2023, [],
     [(4000, "Mountain"), (2090, "San Francisco"), (4120, "menu")], "IMG_0002.HEIC"),
    ("33333333-3333-3333-3333-333333333333", 0.8, (4032, 3024), None, 2024, [3, 5, 1, 2],
     [(4090, "Graduation"), (3000, "Alex")], "IMG_0003.JPG"),
]


def texture(seed: int, size=(480, 360)) -> Image.Image:
    rng = np.random.default_rng(seed)
    noise = (rng.random((size[1] // 8, size[0] // 8, 3)) * 255).astype("uint8")
    return Image.fromarray(noise).resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(3))


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "Test.photoslibrary"
    (lib / "database" / "search").mkdir(parents=True)
    db = sqlite3.connect(lib / "database" / "Photos.sqlite")
    db.executescript("""
        create table ZASSET (Z_PK integer primary key, ZUUID text, ZDIRECTORY text, ZFILENAME text,
            ZOVERALLAESTHETICSCORE real, ZWIDTH int, ZHEIGHT int, ZLATITUDE real, ZLONGITUDE real,
            ZDATECREATED real, ZTRASHEDSTATE int, ZHIDDEN int, ZKIND int);
        create table ZDETECTEDFACE (Z_PK integer primary key, ZASSETFORFACE int, ZAGETYPE int, ZHIDDEN int);
        create table ZADDITIONALASSETATTRIBUTES (Z_PK integer primary key, ZASSET int,
            ZORIGINALFILENAME text, ZTIMEZONEOFFSET int);
    """)
    leo = sqlite3.connect(lib / "database" / "search" / "leo.sqlite")
    leo.executescript("""
        create table lexicon (lexeme_id integer primary key, category int, content text);
        create table items (identifier text, type int, lexeme_ids blob);
    """)
    lexemes: dict[tuple[int, str], int] = {}
    for pk, (uuid, aes, (w, h), gps, year, ages, terms, name) in enumerate(ASSETS, start=1):
        created = (year - 2001) * 365.25 * 86400 + 180 * 86400
        lat, lon = gps or (-180.0, -180.0)
        db.execute("insert into ZASSET values (?,?,?,?,?,?,?,?,?,?,0,0,0)",
                   (pk, uuid, uuid[0], f"{uuid}.heic", aes, w, h, lat, lon, created))
        db.execute("insert into ZADDITIONALASSETATTRIBUTES (ZASSET, ZORIGINALFILENAME, ZTIMEZONEOFFSET) "
                   "values (?,?,?)", (pk, name, -36000))
        for age in ages:
            db.execute("insert into ZDETECTEDFACE (ZASSETFORFACE, ZAGETYPE, ZHIDDEN) values (?,?,0)", (pk, age))
        ids = [lexemes.setdefault(t, len(lexemes) + 1) for t in terms]
        leo.execute("insert into items values (?,1,?)", (uuid, struct.pack(f"<{len(ids)}I", *ids)))
        preview_dir = lib / "resources" / "derivatives" / "masters" / uuid[0]
        preview_dir.mkdir(parents=True, exist_ok=True)
        size = (480, 360) if w > h else (360, 480)
        texture(pk, size).save(preview_dir / f"{uuid}_4_5005_c.jpeg", quality=90)
    # a trashed photo that must never appear
    db.execute("insert into ZASSET values (99,'99999999-9999-9999-9999-999999999999','9','x.heic',1.0,"
               "100,100,null,null,0,1,0,0)")
    for (cat, term), lid in lexemes.items():
        leo.execute("insert into lexicon values (?,?,?)", (lid, cat, term))
    db.commit()
    leo.commit()
    return lib


@pytest.fixture
def preview(library: Path):
    def path(uuid: str) -> Path:
        return library / "resources" / "derivatives" / "masters" / uuid[0] / f"{uuid}_4_5005_c.jpeg"
    return path
