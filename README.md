# photogrep

Search your Apple Photos library from the terminal by what is in the picture, and find which photo an image was cut from.

Photos already knows a lot about every picture: scene labels, places, the people you have named, faces (adults and children), words that appear in the image, and an aesthetic score. Its search bar only uses part of that. photogrep reads the same data and lets you combine it.

```console
$ photogrep search --label Beach --label Sunset --place Maui --max-faces 0 --landscape --top 3
  1  2024-07-12  aes 0.941  faces  0 (kids 0)  4032x3024  IMG_4512.HEIC  1A2B3C4D-...  Kaanapali, Maui  [Beach, Ocean, Sky, Sunset]
  2  2024-07-14  aes 0.902  faces  0 (kids 0)  4032x3024  IMG_4610.HEIC  5E6F7A8B-...  Lahaina, Maui  [Beach, Sunset]
  3  2019-03-02  aes 0.877  faces  0 (kids 0)  4032x3024  IMG_0931.HEIC  9C0D1E2F-...  Wailea, Maui  [Beach, Palm Tree]

$ photogrep match old-slide-photo.jpg
old-slide-photo.jpg
  match    0.97  2025-04-18  IMG_2210.HEIC  Honolulu, Oahu
           3F4A5B6C-7D8E-9F01-2345-6789ABCDEF01
```

Everything runs locally. photogrep never writes to your library and makes no network requests; Photos.app itself downloads iCloud originals when you export.

## Install

Requires macOS 27 or later and Python 3.10 or later.

```console
uv tool install photogrep      # or: pipx install photogrep
```

Your terminal needs **Full Disk Access** (System Settings > Privacy & Security) to read the Photos library. `export` and `show` also ask once for permission to control Photos.

## Commands

### search

Filters combine with AND. Repeating a flag (two `--label`s) means either one.

| Flag | Matches |
|---|---|
| `--label` | scene and event labels: Beach, Hike, Dog, Birthday, Graduation |
| `--place` | place names at any level: a park, neighborhood, city, region, state |
| `--person` | people you have named in Photos |
| `--album` | album and memory titles |
| `--text` | words read from the image (all must appear) |
| `--near LAT,LON,KM` | taken within KM of a point |
| `--since` / `--until` | year range |
| `--min-faces` / `--max-faces` | face count |
| `--min-adults` / `--min-kids` / `--max-kids` | faces by apparent age |
| `--landscape` / `--portrait` | orientation |

Terms match case-insensitively, and part of a term is enough (`--label grad` finds Graduation). Results are ranked by Apple's aesthetic score. Add `--json` for scripts, `--sheet out.jpg` for a contact sheet, or `--export DIR` to save full-resolution copies.

```console
photogrep search --near 37.7694,-122.4862,1 --min-adults 6 --since 2024 --sheet groups.jpg
photogrep search --text menu --album Travel --json
```

### match

Finds the library photo an image came from: a crop, a screenshot of part of it, a resized copy from a slide deck or a chat. Add `--in DIR` to also look through a folder of image files, or `--no-library` to search only folders.

```console
photogrep match screenshot.png --top 3
photogrep match from-the-deck.jpg --in ~/Downloads
```

Scores run up to 1.0. A match scores 0.90 or higher; 0.80 to 0.90 is reported as possible. The exit status is 0 when every image matched and 1 otherwise, like grep. Photos without a readable preview are counted and reported as not compared, never as a non-match. Photos from shared albums and Shared with You are searched too, and labeled as such.

How well it works, measured on a real library of about 64,000 photos: 198 random crops (42% to 100% of the original's width, resized, recompressed as JPEG) were each looked up among 598 photos. The source ranked first for all 198; 192 scored as a match and 5 as possible. No other photo scored 0.90; the highest was 0.83. A full pass over the 64,000-photo library takes about 90 seconds on Apple silicon.

The image needs to cover at least 40% of the original's width. Mirrored, rotated or heavily edited copies will not match, and an image with almost no detail (a patch of sky) is refused rather than guessed at. Burst shots and duplicates of the source can score high, and should.

### labels

Lists the terms Photos has indexed, with how many photos carry each one. Use it to find out what to search for.

```console
photogrep labels --kind scene --top 30
photogrep labels --like park
```

### export and show

```console
photogrep export 3F4A5B6C --to ~/Desktop/picks        # a unique UUID prefix is enough
photogrep export 3F4A5B6C --to ~/Desktop/picks --originals
photogrep show 3F4A5B6C                                # opens Photos on that picture
```

Export goes through Photos.app, which downloads the original from iCloud if needed. Without `--originals` you get the current version with your edits applied.

## How it works

Each run makes a copy-on-write clone of `Photos.sqlite` and the search index `leo.sqlite` (with their WAL files) into a temporary folder. On APFS the clone is instant and takes no extra space. All queries run against the clone.

- `Photos.sqlite` (`ZASSET`, `ZDETECTEDFACE`, `ZADDITIONALASSETATTRIBUTES`) supplies dates, locations, sizes, aesthetic scores, faces and original filenames.
- `database/search/leo.sqlite` is the search index that replaced `psi.sqlite` in macOS 27. `items.identifier` is the asset UUID and `items.lexeme_ids` is a little-endian uint32 array of ids into `lexicon(lexeme_id, category, content)`.
- Every photo has a small preview under `resources/derivatives/masters/`, even when the original lives only in iCloud. Contact sheets and `match` use those previews.
- `match` runs two passes. The coarse pass slides a 64-pixel grayscale thumbnail of your image across every preview at seven scales (normalized cross-correlation, computed with FFTs) and keeps the 25 best. The fine pass rechecks those at 160 pixels, on both the image and its edges, so large shapes and smooth gradients cannot make unrelated photos look alike. It uses up to four worker processes.

## Caveats

These parts of the Photos database are undocumented, so treat them as observations, not a contract:

- Lexicon categories, as seen on macOS 27.0.1: 4000 scene labels, 4090 events, 4120 words in the image, 2060 points of interest, 2070 and 2100 neighborhoods, 2090 cities and named places, 2110 regions, 2130 and 2140 states, 2210 bodies of water, 3000 people, 7010 albums, 7020 memories.
- Children are counted from `ZDETECTEDFACE.ZAGETYPE` values 1 and 2, which look like babies and children. That reading is inferred.
- Labels, faces and text only exist after Photos has finished analyzing the library, which happens while the Mac is idle and plugged in.
- A future macOS update can change any of this.

## Development

```console
uv sync
uv run pytest
```

The tests build a small synthetic library, so they run anywhere.

## License

MIT
