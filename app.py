from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from config import Config, load_config
from dataset import Dataset, StateStore
from translators import ManualTranslationPending, get_translator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("labeler")

app = FastAPI(title="Invoice Labeler")

cfg: Config = load_config()
dataset = Dataset(cfg)
state = StateStore(cfg.state_file)
cfg.cache_folder.mkdir(parents=True, exist_ok=True)
cfg.rotated_folder.mkdir(parents=True, exist_ok=True)

# Mutable at runtime via /api/method, without touching config.yaml.
runtime = {"method": cfg.translation_method}


def cache_path_for(index: int) -> Path:
    stem = Path(dataset.image_name(index)).stem
    return cfg.cache_folder / f"{stem}__{cfg.target_language}.jpg"


def rotated_path_for(index: int) -> Path:
    """Where a rotated copy of the original is stored (if the user rotated it)."""
    stem = Path(dataset.image_name(index)).stem
    return cfg.rotated_folder / f"{stem}.jpg"


def working_original_path(index: int) -> Path:
    """The image the app treats as the original: the rotated copy if one exists,
    otherwise the untouched source file. Used for both display and translation so
    rotations persist and feed back into Google Translate."""
    rotated = rotated_path_for(index)
    return rotated if rotated.exists() else dataset.image_path(index)


# Per-(method, index) locks so an image is translated only once at a time,
# whether the request comes from the live endpoint or a background prefetch.
# Without this, paging onto an image whose prefetch is still running would start
# a second, redundant translation of the same image.
_translation_locks: dict[tuple[str, int], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(method: str, index: int) -> threading.Lock:
    key = (method, index)
    with _locks_guard:
        lock = _translation_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _translation_locks[key] = lock
        return lock


def _translate_to_cache(index: int, method: str) -> Path:
    """Translate image `index` with `method` into the cache and return the cache
    path. Deduplicated: concurrent callers for the same image share one
    translation instead of redoing it. May raise ManualTranslationPending or
    other translator errors to the caller."""
    cache_path = cache_path_for(index)
    if cache_path.exists():
        return cache_path
    with _lock_for(method, index):
        if cache_path.exists():  # filled by another caller while we waited
            return cache_path
        translator = get_translator(cfg, method)
        translated = translator.translate_image(working_original_path(index))
        translated.convert("RGB").save(cache_path, "JPEG", quality=92)
        logger.info("Translated and cached index %d (%s)", index, method)
    return cache_path


def _prefetch(index: int, method: str) -> None:
    """Best-effort background translation so the image is cached before the user
    pages onto it."""
    try:
        _translate_to_cache(index, method)
        logger.info("Prefetched translation for index %d", index)
    except ManualTranslationPending:
        pass  # manual mode needs the headed browser; can't prefetch unattended
    except Exception as exc:
        logger.warning("Prefetch failed for index %d: %s", index, exc)


def _manual_prefetch(index: int) -> None:
    """Manual mode: translate-and-cache the next image in a second browser tab so
    it's ready before the user pages onto it. Deduplicated against the live
    translation of the same image."""
    cache_path = cache_path_for(index)
    if cache_path.exists():
        return
    img_path = working_original_path(index)
    if not img_path.exists():
        return
    with _lock_for("manual", index):
        if cache_path.exists():  # the live request beat us to it
            return
        translator = get_translator(cfg, "manual")
        result = translator.prefetch_in_browser(img_path, output_path=cache_path)
    if result.get("status") == "ok":
        logger.info("Prefetched manual translation for index %d", index)
    else:
        logger.warning("Manual prefetch failed for index %d: %s", index, result.get("error"))


def _queue_prefetch(background_tasks: BackgroundTasks, index: int) -> None:
    """Translate-and-cache the current image and the next one ahead of time, so
    paging forward shows the translation instantly."""
    method = runtime["method"]
    total = len(dataset)
    next_index = index + 1

    if method == "manual":
        # Manual drives a single browser tab, serialized behind the live
        # translation. If the current image isn't cached yet, manual_auto will
        # translate it and chain the next prefetch — prefetching here would jump
        # the queue ahead of the image the user is waiting on. But if the current
        # image is already cached, no live translation is pending, so the chain
        # would never fire; prefetch the next one ourselves to keep it going.
        if cache_path_for(index).exists() and 0 <= next_index < total:
            background_tasks.add_task(_manual_prefetch, next_index)
        return

    for idx in (index, next_index):
        if 0 <= idx < total:
            background_tasks.add_task(_prefetch, idx, method)


def _rotate_clockwise(img: Image.Image, degrees: int) -> Image.Image:
    deg = degrees % 360
    if deg == 0:
        return img
    # PIL's rotate() is counter-clockwise; negate to rotate clockwise.
    return img.rotate(-deg, expand=True)


def _state_payload(index: int) -> dict:
    return {
        "index": index,
        "total": len(dataset),
        "image_name": dataset.image_name(index),
        "row": dataset.row(index),
        "translation_method": runtime["method"],
    }


@app.get("/api/state")
def get_state(background_tasks: BackgroundTasks):
    _queue_prefetch(background_tasks, state.index)
    return _state_payload(state.index)


class NavigateRequest(BaseModel):
    action: str  # "next" | "prev" | "goto"
    index: int | None = None


@app.post("/api/navigate")
def navigate(req: NavigateRequest, background_tasks: BackgroundTasks):
    total = len(dataset)
    if req.action == "next":
        new_index = state.index + 1
    elif req.action == "prev":
        new_index = state.index - 1
    elif req.action == "goto":
        if req.index is None:
            raise HTTPException(400, "goto requires 'index'")
        new_index = req.index
    else:
        raise HTTPException(400, f"Unknown action '{req.action}'")

    new_index = state.set_index(new_index, total)
    _queue_prefetch(background_tasks, new_index)
    return _state_payload(new_index)


class MethodRequest(BaseModel):
    method: str


@app.post("/api/method")
def set_method(req: MethodRequest):
    if req.method not in ("google_cloud", "free", "manual"):
        raise HTTPException(400, f"Unknown method '{req.method}'")
    runtime["method"] = req.method
    return {"translation_method": runtime["method"]}


@app.get("/images/original/{index}")
def get_original_image(index: int):
    if not 0 <= index < len(dataset):
        raise HTTPException(404, "Index out of range")
    image_path = working_original_path(index)
    if not image_path.exists():
        raise HTTPException(404, f"Image file not found: {image_path}")
    return FileResponse(image_path)


@app.get("/images/translated/{index}")
def get_translated_image(index: int):
    if not 0 <= index < len(dataset):
        raise HTTPException(404, "Index out of range")

    cache_path = cache_path_for(index)
    if cache_path.exists():
        return FileResponse(cache_path)

    image_path = working_original_path(index)
    if not image_path.exists():
        raise HTTPException(404, f"Image file not found: {image_path}")

    try:
        cache_path = _translate_to_cache(index, runtime["method"])
    except ManualTranslationPending as exc:
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending",
                "message": str(exc),
                "translate_url": exc.translate_url,
                "expected_cache_path": str(cache_path),
            },
        )
    except Exception as exc:
        logger.exception("Translation failed for index %d", index)
        raise HTTPException(500, f"Translation failed: {exc}")

    return FileResponse(cache_path)


@app.post("/api/manual/auto/{index}")
def manual_auto(index: int, background_tasks: BackgroundTasks):
    """Drive the headed browser: open Google Translate's image tab, upload the
    original image, then screenshot the translated result into the cache so the
    Translated panel can show it like any other method. Once the current image is
    done, kick off a second-tab prefetch of the next one."""
    if not 0 <= index < len(dataset):
        raise HTTPException(404, "Index out of range")
    image_path = working_original_path(index)
    if not image_path.exists():
        raise HTTPException(404, f"Image file not found: {image_path}")

    cache_path = cache_path_for(index)
    # Share work with any in-flight prefetch of this same image (dedup).
    with _lock_for("manual", index):
        if cache_path.exists():
            result = {"status": "ok", "translate_url": cfg.manual_translate_url, "cached": True}
        else:
            translator = get_translator(cfg, "manual")
            result = translator.translate_in_browser(image_path, output_path=cache_path)

    # Prefetch the next image in the second tab while the user reviews this one.
    next_index = index + 1
    if next_index < len(dataset):
        background_tasks.add_task(_manual_prefetch, next_index)

    return result


class RotateRequest(BaseModel):
    degrees: int  # net clockwise rotation to bake into the original, e.g. 90/180/270


@app.post("/api/rotate/{index}")
def rotate_image(index: int, req: RotateRequest):
    """Bake a rotation into a saved copy of the original (never touching the
    source file), drop the stale translation, and re-translate the new orientation."""
    if not 0 <= index < len(dataset):
        raise HTTPException(404, "Index out of range")
    src = working_original_path(index)
    if not src.exists():
        raise HTTPException(404, f"Image file not found: {src}")

    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)  # bake any EXIF orientation first
            rotated = _rotate_clockwise(im, req.degrees)
            out = rotated_path_for(index)
            rotated.convert("RGB").save(out, "JPEG", quality=95)
    except Exception as exc:
        logger.exception("Rotate failed for index %d", index)
        raise HTTPException(500, f"Rotate failed: {exc}")

    # The old translation is for the old orientation; drop it.
    cache_path = cache_path_for(index)
    if cache_path.exists():
        cache_path.unlink()

    # Re-translate the new orientation. Manual mode captures synchronously; other
    # methods will re-translate lazily on the next /images/translated request.
    if runtime["method"] == "manual":
        translator = get_translator(cfg, "manual")
        result = translator.translate_in_browser(out, output_path=cache_path)
        return {"status": result.get("status", "ok"), "rotated": True, **result}
    return {"status": "ok", "rotated": True}


@app.get("/api/config")
def get_config():
    return {
        "image_folder_path": str(cfg.image_folder_path),
        "csv_path": str(cfg.csv_path),
        "image_name_column": cfg.image_name_column,
        "original_language": cfg.original_language,
        "target_language": cfg.target_language,
        "translation_method": runtime["method"],
        "cache_folder": str(cfg.cache_folder),
    }


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.server_host, port=cfg.server_port)
