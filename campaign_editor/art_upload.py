"""Decoding of artwork the editor receives from an upload or a paste: a square image,
checked and returned as a Pillow image in the mode the caller asks for."""

from __future__ import annotations

import base64
import binascii
import io
from typing import Any

from PIL import Image

ART_MIN_PIXELS = 32
ART_MAX_PIXELS = 4096
ART_MAX_BYTES = 12 * 1024 * 1024


def decode_square(image: Any, *, minimum: int = ART_MIN_PIXELS, maximum: int = ART_MAX_PIXELS) -> Image.Image:
    """The image in a data URL or base64 text: square, and between ``minimum`` and ``maximum``
    pixels on a side (``ValueError`` names the problem)."""
    text = str(image or "")
    if text.startswith("data:") and "," in text[:200]:
        text = text.split(",", 1)[1]
    if len(text) > ART_MAX_BYTES * 4 // 3:
        raise ValueError("the image is too large")
    try:
        picture = Image.open(io.BytesIO(base64.b64decode(text, validate=True)))
        picture.load()
    except (binascii.Error, ValueError, OSError):
        raise ValueError("the upload is not an image") from None
    width, height = picture.size
    if width != height:
        raise ValueError(f"the artwork must be square, not {width} x {height}")
    if not minimum <= width <= maximum:
        raise ValueError(f"the artwork must be between {minimum} and {maximum} pixels square")
    return picture
