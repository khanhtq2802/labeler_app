from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import Config

# Extensions tried when a searched name has none (after the configured one).
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")


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
        # Images found by searching the folders that aren't in the CSV. They are
        # appended after the CSV rows (index >= csv_count) so every index-based
        # endpoint (display, translate, rotate, AI) works on them unchanged.
        self._extra_names: list[str] = []

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
        return len(self.df) + len(self._extra_names)

    @property
    def csv_count(self) -> int:
        return len(self.df)

    def in_csv(self, index: int) -> bool:
        return index < len(self.df)

    def image_name(self, index: int) -> str:
        if not self.in_csv(index):
            return self._extra_names[index - len(self.df)]
        return str(self.df.iloc[index][self.cfg.image_name_column])

    def row(self, index: int) -> dict:
        if not self.in_csv(index):
            return {self.cfg.image_name_column: self.image_name(index), "_note": "Ảnh không có trong CSV"}
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
        """Find the index whose image matches `name` exactly, accepting either the
        raw image name (CSV value) or the on-disk filename, with or without the
        file extension. CSV rows win; otherwise the configured image folders are
        searched and a match is added as an extra (non-CSV) entry. Returns None
        when the image is in neither."""
        query = name.strip()
        if not query:
            return None
        for i in range(len(self)):
            raw = self.image_name(i)
            fname = self._filenames[i]
            if query in (raw, fname, Path(raw).stem, Path(fname).stem):
                return i
        return self._add_from_folders(query)

    def _add_from_folders(self, query: str) -> int | None:
        """Look for `query` directly inside the image folders and, if found,
        append it as an extra entry. Only bare filenames are accepted so a query
        can't escape the configured folders."""
        if Path(query).name != query or query in (".", ".."):
            return None
        names = [query, self.cfg.apply_extension(query)]
        for ext in _IMAGE_EXTENSIONS:
            names += [query + ext, query + ext.upper()]
        for fname in dict.fromkeys(names):  # dedup, keep order
            found = [f for f in self.cfg.image_folders if (f / fname).is_file()]
            if found:
                self._extra_names.append(fname)
                self._filenames.append(fname)
                self._candidates.append(found)
                self._choice.append(found[0])
                return len(self) - 1
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
