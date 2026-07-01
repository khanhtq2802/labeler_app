from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from config import (
    Config,
    build_config,
    config_file_path,
    load_raw_or_default,
    save_raw,
)
from dataset import Dataset, StateStore
from translators import ManualTranslationPending, get_translator
import ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("labeler")

app = FastAPI(title="Invoice Labeler")

# These are (re)assigned by _load_runtime_state(). The app boots even when
# config.yaml is missing or broken: `cfg` always ends up usable (falling back to
# the template defaults) so the server and setup screen can run, while `dataset`
# is left None and `setup_error` describes what the user must fix in the web UI.
cfg: Config
dataset: Dataset | None = None
setup_error: str | None = None


# A path that never exists, used to force load_raw_or_default() to fall back to
# the template defaults regardless of any (broken) config.yaml on disk.
_NONEXISTENT_CONFIG = config_file_path().with_name("__labeler_no_such_config__.yaml")


def _ensure_dirs() -> None:
    cfg.cache_folder.mkdir(parents=True, exist_ok=True)
    cfg.rotated_folder.mkdir(parents=True, exist_ok=True)


def _load_runtime_state(path=None) -> None:
    """(Re)load config + dataset into the module globals, tolerating a missing or
    invalid config.yaml. On any problem, `cfg` is left as a usable config (the
    template defaults when the file itself can't be built) and `dataset` is None
    with `setup_error` set, so the UI opens on the setup screen instead of the
    process crashing at import time."""
    global cfg, dataset, setup_error
    try:
        config_path, raw, exists = load_raw_or_default(path)
        cfg = build_config(config_path, raw)
    except Exception as exc:
        # config.yaml is present but can't even be parsed/built (e.g. malformed
        # YAML or missing required keys); fall back to the template defaults,
        # ignoring the broken file, so the server still starts into the setup
        # screen instead of crashing at import time.
        config_path = config_file_path(path)
        _, template_raw, _ = load_raw_or_default(_NONEXISTENT_CONFIG)
        cfg = build_config(config_path, template_raw)
        dataset = None
        setup_error = f"config.yaml không hợp lệ: {exc}"
        _ensure_dirs()
        return

    _ensure_dirs()
    if not exists:
        dataset = None
        setup_error = (
            "Chưa có config.yaml. Điền cấu hình bên dưới rồi nhấn "
            "“Áp dụng & quét lại” để tạo file."
        )
        return
    try:
        dataset = Dataset(cfg)
        setup_error = None
    except Exception as exc:
        dataset = None
        setup_error = f"Không đọc được dữ liệu: {exc}"


_load_runtime_state()
state = StateStore(cfg.state_file)

# Mutable at runtime via /api/method, without touching config.yaml.
# `confirmed` gates labeling behind the startup confirmation screen; it resets to
# False on every server start so the config is re-confirmed each run.
runtime = {"method": cfg.translation_method, "confirmed": False}


def _require_dataset() -> Dataset:
    """Endpoints that need labeled data fail clearly (409) until the config is
    valid, instead of raising an opaque error against a None dataset."""
    if dataset is None:
        raise HTTPException(409, setup_error or "Cấu hình chưa sẵn sàng.")
    return dataset


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
    """Translate-and-cache the current image plus the neighbours on each side, so
    paging in either direction shows the translation instantly. Caching the
    previous image as well keeps backward labeling (end → start of the CSV) just
    as responsive as labeling forward."""
    method = runtime["method"]
    total = len(dataset)
    next_index = index + 1
    prev_index = index - 1

    if method == "manual":
        # Manual drives a single browser tab, serialized behind the live
        # translation. If the current image isn't cached yet, manual_auto will
        # translate it and chain the neighbour prefetches — prefetching here would
        # jump the queue ahead of the image the user is waiting on. But if the
        # current image is already cached, no live translation is pending, so the
        # chain would never fire; prefetch the neighbours ourselves to keep going.
        if cache_path_for(index).exists():
            for idx in (next_index, prev_index):
                if 0 <= idx < total:
                    background_tasks.add_task(_manual_prefetch, idx)
        return

    for idx in (index, next_index, prev_index):
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
    _require_dataset()
    # Clamp first: a stale state file can point past the end of a smaller CSV.
    index = state.clamp(len(dataset))
    _queue_prefetch(background_tasks, index)
    return _state_payload(index)


def _setup_payload() -> dict:
    """Everything the startup confirmation screen needs: the (editable) config plus
    a scan of which CSV images are missing or ambiguous across the image folders.
    Works even before the config is valid: when there's no dataset yet, the scan
    is empty and `ready` is False so the UI keeps the user on the config form."""
    if dataset is not None:
        report = dataset.scan_report()
        total = len(dataset)
    else:
        report = {"missing": [], "conflicts": []}
        total = 0
    csv_path = str(cfg.csv_path)
    return {
        "image_folders": [str(f) for f in cfg.image_folders],
        "csv_path": "" if csv_path == "." else csv_path,
        "image_name_column": cfg.image_name_column,
        "file_extension": cfg.file_extension,
        "original_language": cfg.original_language,
        "target_language": cfg.target_language,
        "translation_method": runtime["method"],
        "ai_provider": cfg.ai_provider,
        "ai_model": cfg.ai_model,
        "ai_default_question": cfg.ai_default_question,
        "total": total,
        "missing": report["missing"],
        "conflicts": report["conflicts"],
        "ready": dataset is not None,
        "error": setup_error,
    }


@app.get("/api/setup")
def get_setup():
    return _setup_payload()


class ConfigUpdateRequest(BaseModel):
    image_folders: list[str]
    csv_path: str
    image_name_column: str
    file_extension: str = ""
    original_language: str = "ja"
    target_language: str = "vi"
    ai_provider: str = "claude"
    ai_model: str = ""
    ai_default_question: str = ""


@app.post("/api/config/update")
def update_config(req: ConfigUpdateRequest):
    """Apply edits made on the confirmation screen: rebuild config + dataset in
    memory, persist to config.yaml, and return a fresh scan. The new config is
    validated (config built, CSV loaded) BEFORE anything is written or swapped in,
    so a bad edit can't corrupt the file or break the running app. Also creates
    config.yaml from scratch (seeded from the template) when none exists yet."""
    global cfg, dataset, setup_error

    folders = [f.strip() for f in req.image_folders if f.strip()]
    if not folders:
        raise HTTPException(400, "Cần ít nhất một thư mục ảnh.")

    config_path, raw, _ = load_raw_or_default()
    new_raw = dict(raw)
    new_raw.pop("image_folder_path", None)  # migrate the legacy key away
    new_raw["image_folders"] = folders
    new_raw["csv_path"] = req.csv_path.strip()
    new_raw["image_name_column"] = req.image_name_column.strip()
    new_raw["file_extension"] = req.file_extension
    new_raw["original_language"] = req.original_language.strip()
    new_raw["target_language"] = req.target_language.strip()
    # Merge the editable AI fields, preserving the file-only keys (use_aiauth,
    # aiauth_host/port, api_key, base_url, max_tokens). An empty model is dropped
    # so build_config falls back to its default rather than persisting "".
    ai_raw = dict(raw.get("ai") or {})
    ai_raw["provider"] = req.ai_provider.strip() or "claude"
    if req.ai_model.strip():
        ai_raw["model"] = req.ai_model.strip()
    else:
        ai_raw.pop("model", None)
    ai_raw["default_question"] = req.ai_default_question
    new_raw["ai"] = ai_raw

    try:
        new_cfg = build_config(config_path, new_raw)
        new_dataset = Dataset(new_cfg)
    except Exception as exc:
        raise HTTPException(400, f"Cấu hình không hợp lệ: {exc}")

    save_raw(config_path, new_raw)
    cfg = new_cfg
    dataset = new_dataset
    setup_error = None
    _ensure_dirs()
    return _setup_payload()


class ConfirmRequest(BaseModel):
    method: str | None = None
    # index -> chosen folder path, for images found in more than one folder.
    choices: dict[int, str] = {}


@app.post("/api/confirm")
def confirm_setup(req: ConfirmRequest):
    """Apply the user's folder choices for ambiguous images, optionally update the
    translation method, and unlock labeling for this run."""
    _require_dataset()
    for index, folder in req.choices.items():
        if 0 <= index < len(dataset):
            dataset.set_choice(index, Path(folder))
    if req.method in ("google_cloud", "manual"):
        runtime["method"] = req.method
    runtime["confirmed"] = True
    index = state.clamp(len(dataset))
    return _state_payload(index)


class NavigateRequest(BaseModel):
    action: str  # "next" | "prev" | "goto"
    index: int | None = None


@app.post("/api/navigate")
def navigate(req: NavigateRequest, background_tasks: BackgroundTasks):
    _require_dataset()
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


class SearchRequest(BaseModel):
    name: str


@app.post("/api/search")
def search_by_name(req: SearchRequest, background_tasks: BackgroundTasks):
    """Jump to the row whose image matches `name` exactly (raw image name or
    on-disk filename, with or without the file extension)."""
    _require_dataset()
    index = dataset.find_by_name(req.name)
    if index is None:
        raise HTTPException(404, f"Không tìm thấy ảnh '{req.name.strip()}'")
    new_index = state.set_index(index, len(dataset))
    _queue_prefetch(background_tasks, new_index)
    return _state_payload(new_index)


class MethodRequest(BaseModel):
    method: str


@app.post("/api/method")
def set_method(req: MethodRequest):
    if req.method not in ("google_cloud", "manual"):
        raise HTTPException(400, f"Unknown method '{req.method}'")
    runtime["method"] = req.method
    return {"translation_method": runtime["method"]}


@app.get("/images/original/{index}")
def get_original_image(index: int):
    _require_dataset()
    if not 0 <= index < len(dataset):
        raise HTTPException(404, "Index out of range")
    image_path = working_original_path(index)
    if not image_path.exists():
        raise HTTPException(404, f"Image file not found: {image_path}")
    return FileResponse(image_path)


@app.get("/images/translated/{index}")
def get_translated_image(index: int):
    _require_dataset()
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
    _require_dataset()
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

    # Prefetch the neighbours in the second tab while the user reviews this one,
    # so paging either forward or backward shows the translation instantly.
    total = len(dataset)
    for neighbour in (index + 1, index - 1):
        if 0 <= neighbour < total:
            background_tasks.add_task(_manual_prefetch, neighbour)

    return result


class RotateRequest(BaseModel):
    degrees: int  # net clockwise rotation to bake into the original, e.g. 90/180/270


@app.post("/api/rotate/{index}")
def rotate_image(index: int, req: RotateRequest):
    """Bake a rotation into a saved copy of the original (never touching the
    source file), drop the stale translation, and re-translate the new orientation."""
    _require_dataset()
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
        "image_folders": [str(f) for f in cfg.image_folders],
        "csv_path": str(cfg.csv_path),
        "image_name_column": cfg.image_name_column,
        "original_language": cfg.original_language,
        "target_language": cfg.target_language,
        "translation_method": runtime["method"],
        "cache_folder": str(cfg.cache_folder),
    }


@app.get("/api/ai/config")
def get_ai_config():
    """What the Original-panel AI box needs: which provider/model is active and
    the default question to pre-fill into the prompt."""
    return {
        "provider": cfg.ai_provider,
        "model": cfg.ai_model,
        "default_question": cfg.ai_default_question,
    }


class AskAIRequest(BaseModel):
    index: int
    # Crop box in ORIGINAL-image pixel coordinates (the served original file).
    x: float
    y: float
    w: float
    h: float
    question: str = ""


@app.post("/api/ai/ask")
def ask_ai(req: AskAIRequest):
    """Crop the selected region of the current original image and ask the
    configured model the question, streaming its text answer back as NDJSON
    (one `{"delta": ...}` object per chunk, or `{"error": ...}` on failure)."""
    _require_dataset()
    if not 0 <= req.index < len(dataset):
        raise HTTPException(404, "Index out of range")
    image_path = working_original_path(req.index)
    if not image_path.exists():
        raise HTTPException(404, f"Image file not found: {image_path}")

    box = {"x": req.x, "y": req.y, "w": req.w, "h": req.h}

    def gen():
        try:
            for chunk in ai.ask_about_region_stream(cfg, image_path, box, req.question):
                yield json.dumps({"delta": chunk}, ensure_ascii=False) + "\n"
        except ai.AIError as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"
        except Exception as exc:
            logger.exception("AI ask failed for index %d", req.index)
            yield json.dumps({"error": f"AI thất bại: {exc}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.server_host, port=cfg.server_port)
