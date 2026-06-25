"""Ask an AI model about a cropped region of an invoice image.

The "Ask AI" button in the Original panel sends a box (in original-image pixel
coordinates) plus a question here. We crop that region, send it to a vision
model, and return the text answer.

Model access goes through the local `aiauth` proxy by default (see
https://github.com/khanhtq2802/aiauth): it injects your Claude Code / Codex
subscription OAuth token, so no API key is needed — you only need
`aiauth serve` running. Set `ai.use_aiauth: false` in config.yaml to call the
provider directly with an API key instead.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps

from config import Config

# Cap the crop's longest edge before sending. Claude downsizes anything larger
# than ~1568px on the long edge anyway, so this keeps the request small without
# losing detail the model would actually use.
_MAX_EDGE = 1568


class AIError(Exception):
    """Raised for any problem producing an AI answer, with a user-facing message."""


def _anthropic_base_url(cfg: Config) -> str | None:
    if cfg.ai_use_aiauth:
        return f"http://{cfg.ai_aiauth_host}:{cfg.ai_aiauth_port}/anthropic"
    return cfg.ai_base_url or None


def _openai_base_url(cfg: Config) -> str | None:
    if cfg.ai_use_aiauth:
        return f"http://{cfg.ai_aiauth_host}:{cfg.ai_aiauth_port}/openai/v1"
    return cfg.ai_base_url or None


def _api_key(cfg: Config) -> str:
    # The aiauth proxy ignores the key (it injects the real OAuth token), but the
    # SDKs still require a non-empty string, so send a placeholder.
    return "aiauth-local" if cfg.ai_use_aiauth else (cfg.ai_api_key or "")


def crop_region(image_path, box: dict) -> Image.Image:
    """Crop `box` ({x, y, w, h} in image pixels) from the file at `image_path`,
    honoring EXIF orientation and clamping to the image bounds. Returns an
    RGB image capped to `_MAX_EDGE` on its longest side."""
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        W, H = im.size
        x = max(0, int(box.get("x", 0)))
        y = max(0, int(box.get("y", 0)))
        right = min(W, x + max(1, int(box.get("w", 0))))
        bottom = min(H, y + max(1, int(box.get("h", 0))))
        if right <= x or bottom <= y:
            raise AIError("Vùng chọn không hợp lệ.")
        crop = im.crop((x, y, right, bottom))

    long_edge = max(crop.size)
    if long_edge > _MAX_EDGE:
        scale = _MAX_EDGE / long_edge
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.LANCZOS,
        )
    return crop


def _encode_jpeg(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _ask_claude(cfg: Config, b64: str, question: str) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise AIError(
            "Chưa cài SDK 'anthropic'. Chạy: pip install anthropic"
        ) from exc

    client = Anthropic(api_key=_api_key(cfg), base_url=_anthropic_base_url(cfg))
    try:
        msg = client.messages.create(
            model=cfg.ai_model,
            max_tokens=cfg.ai_max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": question},
                    ],
                }
            ],
        )
    except Exception as exc:  # surface SDK/proxy errors to the UI
        raise AIError(_explain(exc)) from exc

    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _ask_openai(cfg: Config, b64: str, question: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIError("Chưa cài SDK 'openai'. Chạy: pip install openai") from exc

    client = OpenAI(api_key=_api_key(cfg), base_url=_openai_base_url(cfg))
    try:
        # The aiauth Codex backend speaks the Responses API.
        resp = client.responses.create(
            model=cfg.ai_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}",
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        raise AIError(_explain(exc)) from exc

    return (resp.output_text or "").strip()


def _explain(exc: Exception) -> str:
    """Turn a connection error into a hint about the aiauth proxy not running."""
    text = str(exc)
    if "Connection" in type(exc).__name__ or "connect" in text.lower():
        return (
            "Không kết nối được tới model. Nếu dùng aiauth, hãy chắc chắn đã chạy "
            "`aiauth serve`. Chi tiết: " + text
        )
    return text


def ask_about_region(cfg: Config, image_path, box: dict, question: str) -> str:
    """Crop `box` from `image_path` and ask the configured model `question`
    about it. Returns the answer text or raises AIError with a UI message."""
    question = (question or cfg.ai_default_question).strip()
    if not question:
        raise AIError("Câu hỏi trống.")

    b64 = _encode_jpeg(crop_region(image_path, box))

    if cfg.ai_provider == "openai":
        answer = _ask_openai(cfg, b64, question)
    else:
        answer = _ask_claude(cfg, b64, question)

    if not answer:
        raise AIError("Model không trả về nội dung.")
    return answer
