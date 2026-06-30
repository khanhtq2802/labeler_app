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
        # On-disk filename (extension applied) for each row.
        self._filenames = [
            cfg.apply_extension(self.df.iloc[i][cfg.image_name_column])
            for i in range(len(self.df))
        ]
        # Which configured folder(s) actually contain each row's image, and the
        # folder currently chosen to serve it (first match by default).
        self._candidates: list[list[Path]] = []
        self._choice: list[Path | None] = []
        self._scan()

    def _scan(self) -> None:
        """Locate every row's image across all configured folders."""
        self._candidates = []
        self._choice = []
        for fname in self._filenames:
            found = [
                folder for folder in self.cfg.image_folders if (folder / fname).is_file()
            ]
            self._candidates.append(found)
            self._choice.append(found[0] if found else None)

    def __len__(self) -> int:
        return len(self.df)

    def image_name(self, index: int) -> str:
        return str(self.df.iloc[index][self.cfg.image_name_column])

    def row(self, index: int) -> dict:
        return self.df.iloc[index].to_dict()

    def image_path(self, index: int) -> Path:
        """The chosen file path for this row. Falls back to the first configured
        folder when the image is missing, so callers get a meaningful (though
        non-existent) path to report in a 404."""
        folder = self._choice[index] or self.cfg.image_folders[0]
        return folder / self._filenames[index]

    def candidates(self, index: int) -> list[Path]:
        return self._candidates[index]

    def find_by_name(self, name: str) -> int | None:
        """Find the row index whose image matches `name` exactly, accepting either
        the raw image name (CSV value) or the on-disk filename, with or without the
        file extension. Returns None when nothing matches."""
        query = name.strip()
        if not query:
            return None
        for i in range(len(self.df)):
            raw = self.image_name(i)
            fname = self._filenames[i]
            if query in (raw, fname, Path(raw).stem, Path(fname).stem):
                return i
        return None

    def set_choice(self, index: int, folder: Path) -> bool:
        """Pick which folder serves this row's image. Only accepts a folder that
        actually contains the image. Returns whether the choice was applied."""
        if folder in self._candidates[index]:
            self._choice[index] = folder
            return True
        return False

    def scan_report(self) -> dict:
        """Summarize images that are missing (in no folder) or ambiguous (in more
        than one folder), for the startup confirmation screen."""
        missing = []
        conflicts = []
        for i in range(len(self.df)):
            cands = self._candidates[i]
            if not cands:
                missing.append(
                    {"index": i, "image_name": self.image_name(i), "filename": self._filenames[i]}
                )
            elif len(cands) > 1:
                conflicts.append(
                    {
                        "index": i,
                        "image_name": self.image_name(i),
                        "filename": self._filenames[i],
                        "candidates": [str(c) for c in cands],
                        "chosen": str(self._choice[i]),
                    }
                )
        return {"missing": missing, "conflicts": conflicts}


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

    def clamp(self, total: int) -> int:
        """Pull the remembered index back into range. The state file can outlive
        the dataset it was written for (e.g. a smaller CSV), leaving a stale index
        that would otherwise index past the end of the data."""
        return self.set_index(self._index, total)
