from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_ENV_VAR = "LABELER_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"
EXAMPLE_CONFIG_PATH = Path(__file__).parent / "config.example.yaml"


@dataclass
class Config:
    image_folders: list[Path]
    csv_path: Path
    image_name_column: str
    file_extension: str
    original_language: str
    target_language: str
    translation_method: str
    cache_folder: Path
    rotated_folder: Path
    state_file: Path
    font_path: str
    google_cloud_credentials_json: str
    manual_translate_url: str
    server_host: str
    server_port: int
    # AI reference assistant (ask a model about a cropped region of the original)
    ai_provider: str
    ai_model: str
    ai_use_aiauth: bool
    ai_aiauth_host: str
    ai_aiauth_port: int
    ai_api_key: str
    ai_base_url: str
    ai_default_question: str
    ai_max_tokens: int
    base_dir: Path = field(repr=False)
    config_path: Path = field(repr=False)

    def apply_extension(self, image_name: str) -> str:
        """Return the on-disk filename for a CSV image name, appending the
        configured extension when the name has none."""
        name = str(image_name).strip()
        if not Path(name).suffix and self.file_extension:
            name = name + self.file_extension
        return name


def _resolve_path(base_dir: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def _resolve_image_folders(base_dir: Path, raw: dict) -> list[Path]:
    """Read the image folder(s) from config. Accepts the new `image_folders`
    (a list) or the legacy `image_folder_path` (a single string or a list).
    Duplicate and blank folders are dropped while preserving order. Returns an
    empty list when none are configured yet, so a not-yet-set-up config can still
    be built and edited from the web UI."""
    value = raw.get("image_folders", raw.get("image_folder_path"))
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        value = [value]
    folders: list[Path] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        folder = _resolve_path(base_dir, text)
        if folder not in folders:
            folders.append(folder)
    return folders


def config_file_path(path: str | Path | None = None) -> Path:
    """Resolve which config file is in effect (explicit arg, env var, or default)."""
    return Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)


def load_raw(path: str | Path | None = None) -> tuple[Path, dict]:
    """Read the raw YAML mapping (untouched strings, comments dropped) plus its
    path. Used both to build a Config and to round-trip edits back to disk."""
    config_path = config_file_path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            f"Copy config.example.yaml to config.yaml and edit it first."
        )
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return config_path, raw


def load_raw_or_default(path: str | Path | None = None) -> tuple[Path, dict, bool]:
    """Like `load_raw`, but never raises when config.yaml is absent. Returns
    (config_path, raw, exists). When config.yaml doesn't exist yet, `raw` is
    seeded from config.example.yaml (or {} if that's missing too) so the setup UI
    starts with sensible defaults, while `config_path` still points at
    config.yaml so a later save lands there."""
    config_path = config_file_path(path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return config_path, (yaml.safe_load(f) or {}), True
    raw: dict = {}
    if EXAMPLE_CONFIG_PATH.exists():
        with open(EXAMPLE_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    return config_path, raw, False


def save_raw(config_path: Path, raw: dict) -> None:
    """Persist a raw config mapping back to YAML. Note: PyYAML can't preserve the
    original file's comments, so they are lost on save."""
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def load_config(path: str | Path | None = None) -> Config:
    config_path, raw = load_raw(path)
    return build_config(config_path, raw)


def build_config(config_path: Path, raw: dict) -> Config:
    base_dir = config_path.parent

    google_cloud = raw.get("google_cloud") or {}
    manual = raw.get("manual") or {}
    server = raw.get("server") or {}
    ai = raw.get("ai") or {}

    # csv_path and image_name_column may be blank in a not-yet-configured file;
    # keep them empty here so the config still builds and the web UI can show the
    # blank fields to fill in. The dataset load is what surfaces the real error.
    csv_raw = str(raw.get("csv_path", "") or "").strip()

    return Config(
        image_folders=_resolve_image_folders(base_dir, raw),
        csv_path=_resolve_path(base_dir, csv_raw) if csv_raw else Path(""),
        image_name_column=raw.get("image_name_column", "") or "",
        file_extension=raw.get("file_extension", ""),
        original_language=raw.get("original_language", "ja"),
        target_language=raw.get("target_language", "vi"),
        translation_method=raw.get("translation_method", "manual"),
        cache_folder=_resolve_path(base_dir, raw.get("cache_folder", "./cache")),
        rotated_folder=_resolve_path(base_dir, raw.get("rotated_folder", "./rotated")),
        state_file=_resolve_path(base_dir, raw.get("state_file", "./state.json")),
        font_path=raw.get("font_path", ""),
        google_cloud_credentials_json=google_cloud.get("credentials_json", ""),
        manual_translate_url=manual.get(
            "translate_url", "https://translate.google.com/?sl={source}&tl={target}&op=images"
        ),
        server_host=server.get("host", "127.0.0.1"),
        server_port=int(server.get("port", 8000)),
        ai_provider=(ai.get("provider", "claude") or "claude").strip().lower(),
        ai_model=ai.get("model", "claude-opus-4-8") or "claude-opus-4-8",
        # Route through a locally-running aiauth proxy (no API key needed; reuses
        # the Claude Code / Codex subscription token). When False, the real
        # api_key/base_url below are used instead.
        ai_use_aiauth=bool(ai.get("use_aiauth", True)),
        ai_aiauth_host=ai.get("aiauth_host", "127.0.0.1") or "127.0.0.1",
        ai_aiauth_port=int(ai.get("aiauth_port", 8787)),
        ai_api_key=ai.get("api_key", "") or "",
        ai_base_url=ai.get("base_url", "") or "",
        ai_default_question=ai.get(
            "default_question",
            "Hãy dịch từng phần nội dung trong vùng được chọn sang tiếng Việt "
            "và phân tích ý nghĩa của chúng.",
        ),
        ai_max_tokens=int(ai.get("max_tokens", 1024)),
        base_dir=base_dir,
        config_path=config_path,
    )
