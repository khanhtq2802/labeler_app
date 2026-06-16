from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_ENV_VAR = "LABELER_CONFIG"
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class Config:
    image_folder_path: Path
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
    free_tesseract_lang: str
    manual_translate_url: str
    server_host: str
    server_port: int
    base_dir: Path = field(repr=False)

    def resolve_image_path(self, image_name: str) -> Path:
        name = str(image_name).strip()
        if not Path(name).suffix and self.file_extension:
            name = name + self.file_extension
        return self.image_folder_path / name


def _resolve_path(base_dir: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            f"Copy config.example.yaml to config.yaml and edit it first."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    base_dir = config_path.parent

    google_cloud = raw.get("google_cloud") or {}
    free = raw.get("free") or {}
    manual = raw.get("manual") or {}
    server = raw.get("server") or {}

    return Config(
        image_folder_path=_resolve_path(base_dir, raw["image_folder_path"]),
        csv_path=_resolve_path(base_dir, raw["csv_path"]),
        image_name_column=raw["image_name_column"],
        file_extension=raw.get("file_extension", ""),
        original_language=raw.get("original_language", "ja"),
        target_language=raw.get("target_language", "vi"),
        translation_method=raw.get("translation_method", "manual"),
        cache_folder=_resolve_path(base_dir, raw.get("cache_folder", "./cache")),
        rotated_folder=_resolve_path(base_dir, raw.get("rotated_folder", "./rotated")),
        state_file=_resolve_path(base_dir, raw.get("state_file", "./state.json")),
        font_path=raw.get("font_path", ""),
        google_cloud_credentials_json=google_cloud.get("credentials_json", ""),
        free_tesseract_lang=free.get("tesseract_lang", "jpn"),
        manual_translate_url=manual.get(
            "translate_url", "https://translate.google.com/?sl={source}&tl={target}&op=images"
        ),
        server_host=server.get("host", "127.0.0.1"),
        server_port=int(server.get("port", 8000)),
        base_dir=base_dir,
    )
