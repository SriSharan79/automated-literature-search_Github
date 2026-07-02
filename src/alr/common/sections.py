"""
alr.common.sections
====================

Single source of truth for the ALR "section" concept (Research Problem,
Objective, Methodology, Conclusion, Results, Research Areas, Key Concepts).

This module replaces FIVE previously-duplicated, drifting builders:

  - RAG_BUILDERs/Text_DB_updater.py        :: _build_sections_map(VDB)
  - RAG_BUILDERs/DB_Manager.py              :: _build_sections_map_VDB(VDB)
  - RAG_BUILDERs/querry_excecuter.py        :: _build_sections_map_full(VDB)
  - RAG_BUILDERs/querry_excecuter.py        :: _build_sections_map_RA_KC(VDB)
  - RAG_BUILDERs/Master_excel_DB_Builder.py :: _build_sections_Master_map(VDB, path)
  - Analysis_Evaluation/Data_evaluator.py   :: SECTION_MAP (inline, local var)

KNOWN FIXED HERE:
  Text_DB_updater.py read `VDB.Methodology_DB_excel` / (implicitly)
  `VDB.Methodology_DB_bin`, while querry_excecuter.py read
  `VDB.Methodology_excel` / `VDB.Methodology_bin` (no "_DB_" infix) for the
  *same* underlying attribute. Only one of those attribute names can be
  correct on Vec_DB_Manager. This module standardizes on the "_DB_" form
  (Methodology_DB_excel / Methodology_DB_json / Methodology_DB_bin) for
  consistency with every other section. If Vec_DB_Manager currently defines
  the non-"_DB_" spelling, rename the attribute there rather than adding a
  second alias here.

Usage:
    from alr.common.sections import get_sections_map, SectionField

    # Replaces Text_DB_updater._build_sections_map(VDB)
    sections = get_sections_map(VDB, fields=(SectionField.EXCEL, SectionField.JSON))

    # Replaces DB_Manager._build_sections_map_VDB(VDB)
    sections = get_sections_map(VDB, fields=(SectionField.BIN, SectionField.JSON))

    # Replaces querry_excecuter._build_sections_map_full(VDB)
    sections = get_sections_map(VDB, fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN))

    # Replaces querry_excecuter._build_sections_map_RA_KC(VDB)
    sections = get_sections_map(
        VDB,
        fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN),
        only=("Research Areas", "Key Concepts"),
    )

    # Replaces Master_excel_DB_Builder._build_sections_Master_map(VDB, master_excel_path)
    master_sections = get_master_sections_map(VDB, master_excel_path)

    # Replaces Data_evaluator's inline SECTION_MAP
    eval_sections = get_eval_sections_map(VDB)

If a caller needs the exact old return shape as a drop-in replacement, use
the thin compatibility wrappers at the bottom of this file instead of
`get_sections_map` directly — they are named after the functions they
replace and are safe to import in place of the originals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class SectionField(str, Enum):
    """Which attribute of a section this field refers to on Vec_DB_Manager."""
    EXCEL = "excel"
    JSON = "json"
    BIN = "bin"
    EVAL_EXCEL = "eval_excel"


@dataclass(frozen=True)
class SectionSpec:
    """
    Everything needed to locate every representation of one section.

    `key` is the human-readable section name used as dict keys throughout
    the codebase (and as the JSON key in abstract JSON files) — this must
    stay exactly as-is since it's a stable external contract with stored data.
    """
    key: str
    excel_attr: str        # e.g. "Research_problem_DB_excel" on Vec_DB_Manager
    json_attr: str          # e.g. "Research_problem_DB_json" on Vec_DB_Manager
    bin_attr: str            # e.g. "Research_problem_DB_bin" on Vec_DB_Manager
    master_sheet: str        # sheet name in the combined Master Excel workbook
    eval_excel_attr: str    # e.g. "Research_problem_Eval_excel" on Vec_DB_Manager


# ---------------------------------------------------------------------------
# Canonical registry — the ONE place new sections get added or renamed.
# ---------------------------------------------------------------------------
ALR_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="Research Problem",
        excel_attr="Research_problem_DB_excel",
        json_attr="Research_problem_DB_json",
        bin_attr="Research_problem_DB_bin",
        master_sheet="Research_Problem",
        eval_excel_attr="Research_problem_Eval_excel",
    ),
    SectionSpec(
        key="Objective",
        excel_attr="Objective_DB_excel",
        json_attr="Objective_DB_json",
        bin_attr="Objective_DB_bin",
        master_sheet="Objective",
        eval_excel_attr="Objective_Eval_excel",
    ),
    SectionSpec(
        key="Methodology",
        # NOTE: standardized to the "_DB_" form — see module docstring.
        excel_attr="Methodology_DB_excel",
        json_attr="Methodology_DB_json",
        bin_attr="Methodology_DB_bin",
        master_sheet="Methodology",
        eval_excel_attr="Methodology_Eval_excel",
    ),
    SectionSpec(
        key="Conclusion",
        excel_attr="Conclusion_DB_excel",
        json_attr="Conclusion_DB_json",
        bin_attr="Conclusion_DB_bin",
        master_sheet="Conclusion",
        eval_excel_attr="Conclusion_Eval_excel",
    ),
    SectionSpec(
        key="Results",
        excel_attr="Results_DB_excel",
        json_attr="Results_DB_json",
        bin_attr="Results_DB_bin",
        master_sheet="Results",
        eval_excel_attr="Results_Eval_excel",
    ),
    SectionSpec(
        key="Research Areas",
        excel_attr="Research_Areas_DB_excel",
        json_attr="Research_Areas_DB_json",
        bin_attr="Research_Areas_DB_bin",
        master_sheet="Research_Areas",
        eval_excel_attr="Research_Areas_Eval_excel",
    ),
    SectionSpec(
        key="Key Concepts",
        excel_attr="Key_concepts_DB_excel",
        json_attr="Key_concepts_DB_json",
        bin_attr="Key_concepts_DB_bin",
        master_sheet="Key_Concepts",
        eval_excel_attr="Key_concepts_Eval_excel",
    ),
)

_SECTIONS_BY_KEY = {spec.key: spec for spec in ALR_SECTIONS}

_FIELD_TO_ATTR = {
    SectionField.EXCEL: "excel_attr",
    SectionField.JSON: "json_attr",
    SectionField.BIN: "bin_attr",
    SectionField.EVAL_EXCEL: "eval_excel_attr",
}


def get_sections_map(
    vdb,
    fields: Iterable[SectionField],
    only: Optional[Iterable[str]] = None,
) -> dict[str, tuple]:
    """
    Generic replacement for every `_build_sections_map*` variant.

    Args:
        vdb: a Vec_DB_Manager instance (or anything exposing the same
             attribute names — kept duck-typed rather than importing
             Vec_DB_Manager here, to avoid a circular import with file_manager.py).
        fields: ordered fields to include in each tuple, e.g.
                (SectionField.EXCEL, SectionField.JSON, SectionField.BIN).
                Order in the output tuple matches the order given here.
        only: optional iterable of section keys to restrict the result to
              (e.g. ("Research Areas", "Key Concepts")). Defaults to all
              sections.

    Returns:
        {section_key: (attr_value, attr_value, ...)} matching the requested
        `fields`, in the same order.

    Raises:
        AttributeError: if `vdb` doesn't have one of the expected attributes
                         — this surfaces naming-inconsistency bugs immediately
                         instead of silently returning wrong data.
        KeyError: if `only` references an unknown section key.
    """
    keys = list(only) if only is not None else [spec.key for spec in ALR_SECTIONS]
    result: dict[str, tuple] = {}

    for key in keys:
        if key not in _SECTIONS_BY_KEY:
            raise KeyError(
                f"Unknown section key {key!r}. Valid keys: "
                f"{[s.key for s in ALR_SECTIONS]}"
            )
        spec = _SECTIONS_BY_KEY[key]
        values = tuple(getattr(vdb, getattr(spec, _FIELD_TO_ATTR[f])) for f in fields)
        result[key] = values

    return result


def get_master_sections_map(vdb, master_excel_path) -> dict[str, tuple]:
    """
    Replacement for Master_excel_DB_Builder._build_sections_Master_map(VDB, master_excel_path).

    Returns {section_key: (master_excel_path, sheet_name, json_path)}.
    """
    return {
        spec.key: (master_excel_path, spec.master_sheet, getattr(vdb, spec.json_attr))
        for spec in ALR_SECTIONS
    }


def get_eval_sections_map(vdb) -> dict[str, tuple]:
    """
    Replacement for Data_evaluator's inline SECTION_MAP.

    Returns {section_key: (eval_excel_path, section_key)}.
    """
    return {spec.key: (getattr(vdb, spec.eval_excel_attr), spec.key) for spec in ALR_SECTIONS}


# ---------------------------------------------------------------------------
# Drop-in compatibility wrappers — same names/signatures as the old
# functions they replace, so call sites can switch imports with a one-line
# change and no other edits.
# ---------------------------------------------------------------------------

def build_sections_map(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/Text_DB_updater.py :: _build_sections_map(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.EXCEL, SectionField.JSON))


def build_sections_map_vdb(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/DB_Manager.py :: _build_sections_map_VDB(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.BIN, SectionField.JSON))


def build_sections_map_full(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/querry_excecuter.py :: _build_sections_map_full(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN))


def build_sections_map_ra_kc(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/querry_excecuter.py :: _build_sections_map_RA_KC(VDB)"""
    return get_sections_map(
        vdb,
        fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN),
        only=("Research Areas", "Key Concepts"),
    )


def build_sections_master_map(vdb, master_excel_path) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/Master_excel_DB_Builder.py :: _build_sections_Master_map(VDB, master_excel_path)"""
    return get_master_sections_map(vdb, master_excel_path)


def build_sections_eval_map(vdb) -> dict[str, tuple]:
    """Was: Analysis_Evaluation/Data_evaluator.py :: inline SECTION_MAP"""
    return get_eval_sections_map(vdb)