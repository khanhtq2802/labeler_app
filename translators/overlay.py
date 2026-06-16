from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .base import TextBlock

_FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def _resolve_font_path(font_path: str) -> str | None:
    candidates = [font_path] + _FALLBACK_FONTS
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _load_font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    resolved = _resolve_font_path(font_path or "")
    if resolved:
        return ImageFont.truetype(resolved, size)
    # Last resort: PIL's bundled bitmap font. It won't render Vietnamese
    # diacritics correctly, but keeps the app from crashing.
    return ImageFont.load_default()


def draw_overlay(image: Image.Image, blocks: list[TextBlock], font_path: str) -> Image.Image:
    """Paint over each detected source-text box with white, then draw the
    translated text in its place. This approximates Google Translate's
    image-translation overlay; it works best on plain, light-background
    invoices/receipts.
    """
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)

    for block in blocks:
        if not block.translated_text.strip():
            continue

        pad = 2
        box = (
            max(block.left - pad, 0),
            max(block.top - pad, 0),
            min(block.left + block.width + pad, result.width),
            min(block.top + block.height + pad, result.height),
        )
        draw.rectangle(box, fill="white")

        box_w = box[2] - box[0]
        box_h = box[3] - box[1]
        if box_w <= 0 or box_h <= 0:
            continue

        text = block.translated_text.strip()
        font_size = max(int(box_h * 0.8), 8)
        font = _load_font(font_path, font_size)

        # Shrink font / wrap text until it fits inside the box, capped to
        # avoid pathological loops on very long translated strings.
        for _ in range(6):
            avg_char_w = max(font.getbbox("Ab")[2] / 2, 1)
            chars_per_line = max(int(box_w / avg_char_w), 1)
            lines = textwrap.wrap(text, width=chars_per_line) or [text]
            line_height = font.getbbox("Ag")[3] + 2
            total_h = line_height * len(lines)
            longest_line_w = max(
                (font.getbbox(line)[2] for line in lines), default=0
            )
            if total_h <= box_h and longest_line_w <= box_w:
                break
            font_size = max(font_size - 2, 8)
            font = _load_font(font_path, font_size)
            if font_size <= 8:
                break

        y = box[1]
        for line in lines:
            draw.text((box[0], y), line, fill=(20, 20, 20), font=font)
            y += line_height

    return result
