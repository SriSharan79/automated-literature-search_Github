"""
alr.common.storage_scanner
==========================

Discover DataAnalyzeManager "storage spaces" and download logs under a folder.

A *storage space* is a directory produced by
:class:`alr.common.file_manager.DataAnalyzeManager` (it contains
``Processed_file_registry.xlsx`` and/or the analyzed-data subfolders). A single
folder the user points at may contain **several** such spaces nested at
different depths; this module walks the tree, identifies each space, and marks
it **complete** (registry + at least one analyzed abstract) or **partial**.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from alr.common.file_manager import DataAnalyzeManager


@dataclass
class StorageSpace:
    path: str
    status: str            # "complete" | "partial"
    has_registry: bool
    n_pdfs: int
    n_registry: int
    n_abstracts: int
    present_dirs: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.path).name


def detect_storage_spaces(root) -> list:
    """
    Recursively find every DataAnalyzeManager storage space under ``root``.

    Once a folder is identified as a space, its own subfolders are pruned from
    the walk (they belong to that space, not separate spaces). Returns the
    spaces sorted by path.
    """
    root = Path(root)
    spaces = []
    if not root.exists():
        return spaces

    # Include the root itself as a candidate, then walk downward.
    for dirpath, dirnames, _filenames in os.walk(root):
        info = DataAnalyzeManager.describe_folder(dirpath)
        if info["is_space"]:
            spaces.append(StorageSpace(
                path=info["path"],
                status=info["status"],
                has_registry=info["has_registry"],
                n_pdfs=info["n_pdfs"],
                n_registry=info["n_registry"],
                n_abstracts=info["n_abstracts"],
                present_dirs=info["present_dirs"],
            ))
            # Prune: don't treat this space's internal folders as new spaces.
            dirnames[:] = []

    spaces.sort(key=lambda s: s.path.lower())
    return spaces


def find_download_logs(root) -> list:
    """
    Find all download-log workbooks under ``root``: files whose name contains
    ``_download_log`` and ends with ``.xlsx`` (see File_Downloader._build_paths).
    """
    root = Path(root)
    logs = []
    if not root.exists():
        return logs
    for p in root.rglob("*.xlsx"):
        name = p.name.lower()
        if "_download_log" in name and not name.startswith("~$"):
            logs.append(p)
    logs.sort(key=lambda p: str(p).lower())
    return logs
