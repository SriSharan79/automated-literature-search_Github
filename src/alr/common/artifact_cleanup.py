"""
alr.common.artifact_cleanup
===========================

The :class:`~alr.common.file_manager.DataAnalyzeManager` eagerly creates the full
storage-space folder tree (and per-document sub-folders) up front, so after an
analysis run a storage space often contains folders and files that were never
actually written to — e.g. ``failed_pdfs/`` when nothing failed, per-document
``*_Tables_files`` / ``*_Images_files`` folders when a paper had no tables/images,
or an enrichment sub-folder when that pass was skipped.

This module removes those empty artefacts (zero-byte / whitespace-only / empty
JSON-container files and the now-empty directories that held them) so the on-disk
storage space contains only files and folders that carry real content. The
managed structure is safe to prune: any folder a later step needs is recreated by
``DataAnalyzeManager.__init__`` / ``update_id_files`` (both use ``mkdir`` with
``exist_ok=True``).
"""

from __future__ import annotations

from pathlib import Path

# Small text files whose *content* is one of these (after stripping) count as
# empty even though they are not zero bytes.
_EMPTY_TEXT_CONTENTS = {"", "{}", "[]", "null", "{ }", "[ ]", "[]\n", "{}\n"}
# Only read files up to this size to decide emptiness; anything larger has content.
_MAX_PEEK_BYTES = 8192


def _is_empty_file(path: Path) -> bool:
    """True if ``path`` is a regular file with no meaningful content."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0:
        return True
    if size > _MAX_PEEK_BYTES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="strict").strip()
    except (UnicodeDecodeError, OSError):
        # Binary or unreadable but non-zero -> treat as having content.
        return False
    return text in _EMPTY_TEXT_CONTENTS


def prune_empty_artifacts(root, should_cancel=None):
    """
    Remove empty files and empty directories under ``root`` (bottom-up), leaving
    ``root`` itself in place even if it ends up empty.

    A file is "empty" when it is zero bytes or its (small) text content is only
    whitespace / an empty JSON container. A directory is removed once it holds no
    remaining entries. Returns ``(removed_files, removed_dirs)`` as lists of str
    paths. All failures are swallowed so cleanup never breaks the caller.
    """
    root = Path(root)
    removed_files: list[str] = []
    removed_dirs: list[str] = []
    if not root.is_dir():
        return removed_files, removed_dirs

    # Walk deepest-first so a directory is visited after its children, letting a
    # folder that only held empty files become empty and get removed in one pass.
    all_dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )

    def _sweep_files(folder: Path):
        for child in folder.iterdir():
            if child.is_file() and _is_empty_file(child):
                try:
                    child.unlink()
                    removed_files.append(str(child))
                except OSError:
                    pass

    for d in all_dirs:
        if should_cancel is not None and should_cancel():
            break
        _sweep_files(d)
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed_dirs.append(str(d))
        except OSError:
            pass

    # Finally sweep empty files sitting directly in root (root is never removed).
    if should_cancel is None or not should_cancel():
        _sweep_files(root)

    if removed_files or removed_dirs:
        print(f"🧹 Cleanup: removed {len(removed_files)} empty file(s) and "
              f"{len(removed_dirs)} empty folder(s) from {root}")
    return removed_files, removed_dirs


def _top_level_only(folders):
    """
    Drop any folder that already sits inside another one in the list -- pruning
    the parent walks the child anyway, so keeping both would re-walk the tree.
    """
    roots = []
    for folder in sorted(folders, key=len):
        path = Path(folder)
        if not any(_is_within(path, Path(kept)) for kept in roots):
            roots.append(folder)
    return roots


def _is_within(path: Path, parent: Path) -> bool:
    """True if ``path`` is ``parent`` or lives underneath it."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def prune_touched_folders(should_cancel=None):
    """
    Prune every managed folder tree opened since the last call.

    The managers create their whole folder tree up front, so any pass that opens
    one can leave empty folders behind. Each manager registers its root
    (``file_manager.register_managed_folder``); this drains that registry and
    prunes the top-level roots, which lets **every** run clean up after itself
    without each pass having to remember to ask for it.

    Returns ``(removed_files, removed_dirs)`` totalled across the roots.
    """
    from alr.common.file_manager import take_managed_folders

    files: list[str] = []
    dirs: list[str] = []
    for root in _top_level_only(take_managed_folders()):
        if should_cancel is not None and should_cancel():
            break
        f, d = prune_empty_artifacts(root, should_cancel=should_cancel)
        files.extend(f)
        dirs.extend(d)
    return files, dirs
