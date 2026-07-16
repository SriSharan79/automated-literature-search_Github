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

# ---------------------------------------------------------------------------
# RAG sections for the OTHER analysis JSONs: the Introduction analysis
# ({uuid}_Intro.json) and the Results & Conclusion analysis
# ({uuid}_Results_Conclusion.json). Registered exactly like ALR_SECTIONS so
# the text/vector DB builders and the query executor can treat every
# analyzed attribute uniformly; ALR_SECTIONS itself stays abstract-only for
# backward compatibility (every map builder defaults to it).
# ---------------------------------------------------------------------------
INTRO_RAG_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="Background",
        excel_attr="Background_DB_excel",
        json_attr="Background_DB_json",
        bin_attr="Background_DB_bin",
        master_sheet="Background",
        eval_excel_attr="Background_Eval_excel",
    ),
    SectionSpec(
        key="Motivation",
        excel_attr="Motivation_DB_excel",
        json_attr="Motivation_DB_json",
        bin_attr="Motivation_DB_bin",
        master_sheet="Motivation",
        eval_excel_attr="Motivation_Eval_excel",
    ),
    SectionSpec(
        key="Gaps & Limitations",
        excel_attr="Gaps_Limitations_DB_excel",
        json_attr="Gaps_Limitations_DB_json",
        bin_attr="Gaps_Limitations_DB_bin",
        master_sheet="Gaps_Limitations",
        eval_excel_attr="Gaps_Limitations_Eval_excel",
    ),
    SectionSpec(
        key="RQs & Scope",
        excel_attr="RQs_Scope_DB_excel",
        json_attr="RQs_Scope_DB_json",
        bin_attr="RQs_Scope_DB_bin",
        master_sheet="RQs_Scope",
        eval_excel_attr="RQs_Scope_Eval_excel",
    ),
)

RESCON_RAG_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="Results Mentioned",
        excel_attr="Results_Mentioned_DB_excel",
        json_attr="Results_Mentioned_DB_json",
        bin_attr="Results_Mentioned_DB_bin",
        master_sheet="Results_Mentioned",
        eval_excel_attr="Results_Mentioned_Eval_excel",
    ),
    SectionSpec(
        key="Limitations or Boundary Conditions",
        excel_attr="Limitations_Boundary_DB_excel",
        json_attr="Limitations_Boundary_DB_json",
        bin_attr="Limitations_Boundary_DB_bin",
        # Excel sheet names are capped at 31 characters.
        master_sheet="Limitations_Boundary_Conditions",
        eval_excel_attr="Limitations_Boundary_Eval_excel",
    ),
    SectionSpec(
        key="Summary of the Content",
        excel_attr="Content_Summary_DB_excel",
        json_attr="Content_Summary_DB_json",
        bin_attr="Content_Summary_DB_bin",
        master_sheet="Content_Summary",
        eval_excel_attr="Content_Summary_Eval_excel",
    ),
    SectionSpec(
        key="Future Work",
        excel_attr="Future_Work_DB_excel",
        json_attr="Future_Work_DB_json",
        bin_attr="Future_Work_DB_bin",
        master_sheet="Future_Work",
        eval_excel_attr="Future_Work_Eval_excel",
    ),
    SectionSpec(
        key="Outlook",
        excel_attr="Outlook_DB_excel",
        json_attr="Outlook_DB_json",
        bin_attr="Outlook_DB_bin",
        master_sheet="Outlook",
        eval_excel_attr="Outlook_Eval_excel",
    ),
)

# Every RAG-queryable section, in canonical display/build order.
ALL_RAG_SECTIONS: tuple[SectionSpec, ...] = (
    ALR_SECTIONS + INTRO_RAG_SECTIONS + RESCON_RAG_SECTIONS
)

# Which analysis JSON provides each section key.
RAG_SOURCE_BY_KEY: dict[str, str] = {
    **{spec.key: "abstract" for spec in ALR_SECTIONS},
    **{spec.key: "intro" for spec in INTRO_RAG_SECTIONS},
    **{spec.key: "rescon" for spec in RESCON_RAG_SECTIONS},
}

_SECTIONS_BY_KEY = {spec.key: spec for spec in ALL_RAG_SECTIONS}

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


def get_master_sections_map(vdb, master_excel_path, only: Optional[Iterable[str]] = None) -> dict[str, tuple]:
    """
    Replacement for Master_excel_DB_Builder._build_sections_Master_map(VDB, master_excel_path).

    Returns {section_key: (master_excel_path, sheet_name, json_path)}.
    ``only`` restricts (or extends, with intro/rescon keys) the sections;
    defaults to the abstract sections like everything else.
    """
    specs = ALR_SECTIONS if only is None else tuple(_SECTIONS_BY_KEY[k] for k in only)
    return {
        spec.key: (master_excel_path, spec.master_sheet, getattr(vdb, spec.json_attr))
        for spec in specs
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

def build_sections_map(vdb, only: Optional[Iterable[str]] = None) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/Text_DB_updater.py :: _build_sections_map(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.EXCEL, SectionField.JSON), only=only)


def build_sections_map_vdb(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/DB_Manager.py :: _build_sections_map_VDB(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.BIN, SectionField.JSON))


def build_sections_map_full(vdb, only: Optional[Iterable[str]] = None) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/querry_excecuter.py :: _build_sections_map_full(VDB)"""
    return get_sections_map(vdb, fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN), only=only)


def build_sections_map_ra_kc(vdb) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/querry_excecuter.py :: _build_sections_map_RA_KC(VDB)"""
    return get_sections_map(
        vdb,
        fields=(SectionField.EXCEL, SectionField.JSON, SectionField.BIN),
        only=("Research Areas", "Key Concepts"),
    )
    

def build_sections_map_vdb_excel(vdb, only: Optional[Iterable[str]] = None) -> dict[str, tuple]:
    """(BIN, EXCEL) variant of build_sections_map_vdb: the vector sync now
    embeds the same positional Content column the query executor aligns
    against, instead of the section JSON which could drift ahead of it."""
    return get_sections_map(vdb, fields=(SectionField.BIN, SectionField.EXCEL), only=only)


def build_sections_master_map(vdb, master_excel_path, only: Optional[Iterable[str]] = None) -> dict[str, tuple]:
    """Was: RAG_BUILDERs/Master_excel_DB_Builder.py :: _build_sections_Master_map(VDB, master_excel_path)"""
    return get_master_sections_map(vdb, master_excel_path, only=only)


def build_sections_eval_map(vdb) -> dict[str, tuple]:
    """Was: Analysis_Evaluation/Data_evaluator.py :: inline SECTION_MAP"""
    return get_eval_sections_map(vdb)


# ---------------------------------------------------------------------------
# Introduction sections — the analyzed-introduction JSON counterpart of
# ALR_SECTIONS. The Introduction analyzer writes {uuid}_Intro.json with these
# keys plus the identified full-introduction text; evaluation treats them the
# same way data_evaluator treats the abstract sections.
# ---------------------------------------------------------------------------

# The key store_to_json_with_text writes for the raw texts.
ABSTRACT_TEXT_KEY = "Abstract Text identified:"
INTRO_TEXT_KEY = "Introduction Text identified:"

# Intro JSON key -> Vec_DB_Manager eval-workbook attribute.
INTRO_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Background", "Background_Eval_excel"),
    ("Motivation", "Motivation_Eval_excel"),
    ("Gaps & Limitations", "Gaps_Limitations_Eval_excel"),
    ("RQs & Scope", "RQs_Scope_Eval_excel"),
)


def build_intro_sections_eval_map(vdb) -> dict[str, tuple]:
    """
    Introduction counterpart of :func:`build_sections_eval_map`:
    ``{intro_section_key: (eval_excel_path, intro_section_key)}``.
    """
    return {key: (getattr(vdb, attr), key) for key, attr in INTRO_SECTIONS}


# ---------------------------------------------------------------------------
# Batch metric-evaluation workbooks (lexical / distance / cosine): each metric
# kind records into its own dated workbook per target, and a combined overview
# workbook holds all metric data together. Single source of truth for which
# Vec_DB_Manager attribute each (target, kind) pair maps to.
# ---------------------------------------------------------------------------

# target -> {metric kind or "overview": Vec_DB_Manager attribute}
METRIC_WORKBOOK_ATTRS = {
    "abstract": {
        "lexical": "Abstract_Lexical_Metrics",
        "distance": "Abstract_Distance_Metrics",
        "cosine": "Abstract_Cosine_Metrics",
        "overview": "Abstract_Metrics_Overview",
    },
    "intro": {
        "lexical": "Introduction_Lexical_Metrics",
        "distance": "Introduction_Distance_Metrics",
        "cosine": "Introduction_Cosine_Metrics",
        "overview": "Introduction_Metrics_Overview",
    },
}

def build_metric_workbooks_map(vdb, target="abstract") -> dict[str, "object"]:
    """
    Return ``{metric_kind_or_"overview": workbook_path}`` for a target
    ("abstract" or "intro"), resolved against a Vec_DB_Manager instance.
    """
    attrs = METRIC_WORKBOOK_ATTRS[target if target in METRIC_WORKBOOK_ATTRS else "abstract"]
    return {kind: getattr(vdb, attr) for kind, attr in attrs.items()}