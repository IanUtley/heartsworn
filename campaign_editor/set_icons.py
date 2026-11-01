"""Set icons for custom sets: the picture a set's card frames are meant to show.

The client draws a set's frame icon from the ``SetIcon<NN>`` sprites of its card frame atlas
(``Hex_Data/resources.assets``), and a custom set has no sprite there. Until that atlas is
patched, an uploaded icon is kept for the editor's preview only: ``custom_sets/set_icons/
<set GUID>.png``, a 64 x 64 RGBA PNG (the frame draws the icon at about 25 to 34 px, so 64 px
downscales cleanly). Its alpha channel is kept, since the icon sits over the frame.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import art_upload  # noqa: E402
import cards  # noqa: E402
from PIL import Image  # noqa: E402

SET_ICON_PIXELS = 64
SET_ICON_FOLDER = "set_icons"
_LOCK = threading.Lock()


def _custom_set(set_guid: Any) -> dict[str, Any]:
    guid = str(set_guid or "").strip().lower()
    entry = next((e for e in cards.card_sets() if e["guid"] == guid), None)
    if entry is None:
        raise LookupError(f"no such card set: {set_guid}")
    return entry


def icon_url(set_guid: str) -> str:
    """The URL of a set's icon, or "" when none is saved."""
    guid = str(set_guid or "").lower()
    path = cards.CUSTOM_DIR / SET_ICON_FOLDER / f"{guid}.png"
    return f"/custom/{SET_ICON_FOLDER}/{guid}.png" if path.is_file() else ""


def set_icon(set_guid: Any) -> dict[str, Any]:
    entry = _custom_set(set_guid)
    return {"set_guid": entry["guid"], "set_name": entry["name"], "source": entry["source"],
            "url": icon_url(entry["guid"]), "size": SET_ICON_PIXELS}


def save_set_icon(set_guid: Any, image: Any) -> dict[str, Any]:
    entry = _custom_set(set_guid)
    if entry["source"] != "custom":
        raise ValueError("set icons are saved for a custom set")
    picture = art_upload.decode_square(image).convert("RGBA")
    if picture.size != (SET_ICON_PIXELS, SET_ICON_PIXELS):
        picture = picture.resize((SET_ICON_PIXELS, SET_ICON_PIXELS), Image.LANCZOS)
    folder = cards.CUSTOM_DIR / SET_ICON_FOLDER
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{entry['guid']}.png"
        temporary = target.with_name(target.name + ".tmp")
        picture.save(temporary, format="PNG")
        os.replace(temporary, target)
    return set_icon(entry["guid"])
