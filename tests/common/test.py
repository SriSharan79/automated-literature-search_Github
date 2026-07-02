#!/usr/bin/env python3
"""
verify_phase5.py
==================

Runs the Phase 5 verification checks:
  1. Imports every module under src/alr/ and reports any that fail
     (catches circular imports, missing symbols after 3f deletions,
     and runtime AttributeErrors like the Methodology bug — none of
     which a static checker like Pylance will catch).
  2. Scans for leftover sys.path.extend(...) blocks (Phase 3a not done yet).
  3. Scans for leftover references to the old underscored section-map
     function names (_build_sections_map*, _build_sections_Master_map)
     that should have been deleted/renamed in Phase 3f.
  4. Checks that nothing under src/alr/ imports from archive/ or notebooks/
     (those should be dead ends, not dependencies).

This assumes you've already run `pip install -e .` from the repo root
(Phase 5.1) so `import alr...` resolves the same way this script sees it.

Usage:
    Edit REPO_PATH below, then run:
        python verify_phase5.py
"""

from __future__ import annotations

import importlib
import re
import sys
import traceback
from pathlib import Path


def find_alr_modules(repo_root: Path) -> list[str]:
    """Convert every src/alr/**/*.py file into its dotted module name."""
    alr_root = repo_root / "src" / "alr"
    modules = []
    for path in sorted(alr_root.rglob("*.py")):
        rel = path.relative_to(repo_root / "src").with_suffix("")
        parts = rel.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules.append(".".join(parts))
    return modules


def run_import_smoke_test(modules: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    passed, failed = [], []
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            passed.append(mod_name)
        except Exception:
            failed.append((mod_name, traceback.format_exc()))
    return passed, failed


LEFTOVER_SYS_PATH_RE = re.compile(r"sys\.path\.extend\(")
LEFTOVER_UNDERSCORE_SECTION_FN_RE = re.compile(
    r"\b(_build_sections_map\w*|_build_sections_Master_map)\b"
)
ARCHIVE_OR_NOTEBOOK_IMPORT_RE = re.compile(
    r"^\s*(from|import)\s+(archive|notebooks)\b", re.MULTILINE
)


def scan_repo_text(repo_root: Path) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {
        "sys.path.extend still present": [],
        "old underscored section-map names still referenced": [],
        "src/alr importing from archive/ or notebooks/": [],
    }
    for path in (repo_root / "src" / "alr").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(repo_root))

        if LEFTOVER_SYS_PATH_RE.search(text):
            findings["sys.path.extend still present"].append(rel)
        if LEFTOVER_UNDERSCORE_SECTION_FN_RE.search(text):
            findings["old underscored section-map names still referenced"].append(rel)
        if ARCHIVE_OR_NOTEBOOK_IMPORT_RE.search(text):
            findings["src/alr importing from archive/ or notebooks/"].append(rel)

    return {k: v for k, v in findings.items() if v}


def main(repo_path: str) -> int:
    repo_root = Path(repo_path).expanduser().resolve()

    if not (repo_root / "src" / "alr").is_dir():
        print(f"Error: {repo_root / 'src' / 'alr'} not found — run this after Phase 2/3.",
              file=sys.stderr)
        return 1

    print("== 5.3a: import smoke test ==")
    modules = find_alr_modules(repo_root)
    print(f"Found {len(modules)} modules under src/alr/\n")

    passed, failed = run_import_smoke_test(modules)

    print(f"PASSED: {len(passed)}")
    print(f"FAILED: {len(failed)}\n")

    if failed:
        print("== Failures ==")
        for mod_name, tb in failed:
            print(f"\n--- {mod_name} ---")
            print(tb)

    print("\n== 5.3b/5.4: leftover migration artifacts ==")
    findings = scan_repo_text(repo_root)
    if not findings:
        print("None found — sys.path.extend, old section-map names, and "
              "archive/notebook imports are all clean.")
    else:
        for category, files in findings.items():
            print(f"\n  {category}:")
            for f in files:
                print(f"    {f}")

    print("\n== Summary ==")
    ok = not failed and not findings
    print("ALL CLEAR — safe to proceed to manual entry-point smoke runs (5.5)."
          if ok else
          "ISSUES FOUND — resolve the above before committing / running entry points.")
    return 0 if ok else 1


if __name__ == "__main__":
    # ---- Settings: edit this, then run `python verify_phase5.py` ----
    REPO_PATH = r"C:\Users\kata_du\git\automated-literature-search_Github"   # <- set this to your local repo path
    # -------------------------------------------------------------------

    raise SystemExit(main(REPO_PATH))
