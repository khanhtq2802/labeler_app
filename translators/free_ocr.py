from __future__ import annotations

from pathlib import Path

from PIL import Image

from .base import BaseTranslator, TextBlock
from .overlay import draw_overlay


class FreeOcrTranslator(BaseTranslator):
    """No-account pipeline: local Tesseract OCR + the free (unofficial)
    Google Translate web endpoint via the `deep-translator` package.

    Requires the `tesseract-ocr` binary and the source language's traineddata
    to be installed on the system (e.g. `sudo apt install tesseract-ocr
    tesseract-ocr-jpn`). Quality is noticeably lower than the Google Cloud
    method, especially for stylised receipt fonts.
    """

    def translate_image(self, image_path: Path) -> Image.Image:
        import pytesseract
        from deep_translator import GoogleTranslator

        image = Image.open(image_path)
        data = pytesseract.image_to_data(
            image,
            lang=self.cfg.free_tesseract_lang,
            output_type=pytesseract.Output.DICT,
        )

        lines: dict[tuple[int, int, int], dict] = {}
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            if not text:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            left, top = data["left"][i], data["top"][i]
            right, bottom = left + data["width"][i], top + data["height"][i]
            if key not in lines:
                lines[key] = {"text": [text], "left": left, "top": top, "right": right, "bottom": bottom}
            else:
                entry = lines[key]
                entry["text"].append(text)
                entry["left"] = min(entry["left"], left)
                entry["top"] = min(entry["top"], top)
                entry["right"] = max(entry["right"], right)
                entry["bottom"] = max(entry["bottom"], bottom)

        blocks = [
            TextBlock(
                left=entry["left"],
                top=entry["top"],
                width=entry["right"] - entry["left"],
                height=entry["bottom"] - entry["top"],
                source_text="".join(entry["text"]),
            )
            for entry in lines.values()
        ]

        if blocks:
            translator = GoogleTranslator(
                source=self.cfg.original_language, target=self.cfg.target_language
            )
            translated = translator.translate_batch([b.source_text for b in blocks])
            for block, text in zip(blocks, translated):
                block.translated_text = text or ""

        return draw_overlay(image, blocks, self.cfg.font_path)
