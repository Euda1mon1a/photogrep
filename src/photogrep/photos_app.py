"""Talk to Photos.app through AppleScript: export full-resolution files, or show a photo."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

UUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


def _item(uuid: str) -> str:
    if not UUID.match(uuid):
        raise ValueError(f"not a Photos UUID: {uuid}")
    return f'media item id "{uuid.upper()}/L0/001"'


def _quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run(lines: list[str]) -> None:
    subprocess.run(["osascript", "-e", "\n".join(lines)], check=True)


def export(uuids: list[str], dest: Path, originals: bool = False) -> list[Path]:
    """Export each photo into its own subfolder of dest. Photos downloads iCloud-only originals.

    Without originals, Photos exports the current version with your edits applied.
    """
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    using = "with using originals" if originals else "without using originals"
    folders, lines = [], ["with timeout of 1800 seconds", 'tell application "Photos"']
    for k, uuid in enumerate(uuids, start=1):
        folder = dest / f"{k:02d}_{uuid[:8]}"
        folder.mkdir(exist_ok=True)
        folders.append(folder)
        lines.append(f"  export {{{_item(uuid)}}} to (POSIX file {_quote(folder)}) {using}")
    _run(lines + ["end tell", "end timeout"])
    return folders


def show(uuid: str) -> None:
    """Bring Photos forward with this photo selected."""
    _run(['tell application "Photos"', "  activate", f"  spotlight {_item(uuid)}", "end tell"])
