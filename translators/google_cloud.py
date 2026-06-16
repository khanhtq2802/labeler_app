from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from .base import BaseTranslator, TextBlock
from .overlay import draw_overlay


class GoogleCloudTranslator(BaseTranslator):
    """Paid pipeline: Cloud Vision OCR (document_text_detection) + Cloud
    Translation. Best accuracy, especially for Japanese. Requires a GCP
    service account with both APIs enabled, set via
    google_cloud.credentials_json in config.yaml or the
    GOOGLE_APPLICATION_CREDENTIALS environment variable.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        if cfg.google_cloud_credentials_json:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cfg.google_cloud_credentials_json

    def translate_image(self, image_path: Path) -> Image.Image:
        from google.cloud import translate_v2, vision

        image = Image.open(image_path)

        vision_client = vision.ImageAnnotatorClient()
        with open(image_path, "rb") as f:
            content = f.read()
        response = vision_client.document_text_detection(image=vision.Image(content=content))
        if response.error.message:
            raise RuntimeError(f"Cloud Vision error: {response.error.message}")

        blocks: list[TextBlock] = []
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    words = []
                    for word in paragraph.words:
                        words.append("".join(symbol.text for symbol in word.symbols))
                    text = "".join(words) if self.cfg.original_language == "ja" else " ".join(words)
                    if not text.strip():
                        continue
                    xs = [v.x for v in paragraph.bounding_box.vertices]
                    ys = [v.y for v in paragraph.bounding_box.vertices]
                    blocks.append(
                        TextBlock(
                            left=min(xs),
                            top=min(ys),
                            width=max(xs) - min(xs),
                            height=max(ys) - min(ys),
                            source_text=text,
                        )
                    )

        if blocks:
            translate_client = translate_v2.Client()
            results = translate_client.translate(
                [b.source_text for b in blocks],
                source_language=self.cfg.original_language,
                target_language=self.cfg.target_language,
            )
            for block, result in zip(blocks, results):
                block.translated_text = result.get("translatedText", "")

        return draw_overlay(image, blocks, self.cfg.font_path)
