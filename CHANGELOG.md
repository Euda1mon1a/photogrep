# Changelog

## Unreleased

- `match` accepts the search filters (`--since`, `--place`, `--label` and the rest) to compare against fewer photos.
- `labels --json`.
- Exit status follows grep everywhere: 0 found, 1 nothing found, 2 error. `search` and `labels` now return 1 when nothing matches, and errors return 2 instead of 1.
- `search` reports the total on stderr when `--top` limits the list ("20 of 312 photos").
- README: install with Homebrew; a section on use from scripts and AI agents.
- CI: actions pinned to commits; read-only token.

## 0.1.0 (2026-10-14)

First release: `search`, `match`, `labels`, `export` and `show` for Apple Photos on macOS 27.
