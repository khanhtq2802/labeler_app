from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import Config


class Dataset:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # dtype=str keeps filenames like "0042" from being read as numbers.
        self.df = pd.read_csv(cfg.csv_path, dtype=str, keep_default_na=False)
        if cfg.image_name_column not in self.df.columns:
            raise ValueError(
                f"image_name_column '{cfg.image_name_column}' not found in CSV columns: "
                f"{list(self.df.columns)}"
            )

    def __len__(self) -> int:
        return len(self.df)

    def image_name(self, index: int) -> str:
        return str(self.df.iloc[index][self.cfg.image_name_column])

    def row(self, index: int) -> dict:
        return self.df.iloc[index].to_dict()

    def image_path(self, index: int) -> Path:
        return self.cfg.resolve_image_path(self.image_name(index))


class StateStore:
    """Remembers which row the user was last looking at, across restarts."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._index = 0
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                self._index = int(data.get("index", 0))
            except (json.JSONDecodeError, ValueError):
                self._index = 0

    @property
    def index(self) -> int:
        return self._index

    def set_index(self, index: int, total: int) -> int:
        self._index = max(0, min(index, total - 1)) if total else 0
        self.state_file.write_text(json.dumps({"index": self._index}), encoding="utf-8")
        return self._index
