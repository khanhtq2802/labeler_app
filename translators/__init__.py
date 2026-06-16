from __future__ import annotations

from config import Config

from .base import BaseTranslator, ManualTranslationPending
from .free_ocr import FreeOcrTranslator
from .google_cloud import GoogleCloudTranslator
from .manual import ManualTranslator

_METHODS = {
    "google_cloud": GoogleCloudTranslator,
    "free": FreeOcrTranslator,
    "manual": ManualTranslator,
}


def get_translator(cfg: Config, method: str | None = None) -> BaseTranslator:
    method = method or cfg.translation_method
    try:
        cls = _METHODS[method]
    except KeyError:
        raise ValueError(
            f"Unknown translation_method '{method}'. Choose one of: {list(_METHODS)}"
        )
    return cls(cfg)


__all__ = ["get_translator", "BaseTranslator", "ManualTranslationPending"]
