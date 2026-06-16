from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from config import Config


class ManualTranslationPending(Exception):
    """Raised by the manual translator when no human-produced translation exists yet."""

    def __init__(self, message: str, translate_url: str):
        super().__init__(message)
        self.translate_url = translate_url


@dataclass
class TextBlock:
    # Pixel bounding box of the detected text, in the original image's coordinates.
    left: int
    top: int
    width: int
    height: int
    source_text: str
    translated_text: str = ""


class BaseTranslator:
    """Produces a translated-overlay image for one invoice image, given a cache miss."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def translate_image(self, image_path: Path) -> Image.Image:
        raise NotImplementedError
