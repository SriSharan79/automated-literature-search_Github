"""
alr.data_analysis.section_resolver
==================================

Shared section-location and attribute-completion logic for the three analysis
targets (Abstract, Introduction, Results & Conclusion).

Before this module each ``*_Analyzer.py`` had its own private ladder for
"where is this section in the document?" and simply accepted
``"No information available"`` for every attribute the LLM could not fill.
Two behaviours are added here, once, for all three targets:

1. **Attribute top-up.** After the first extraction pass, any attribute still
   holding ``No information available`` triggers ONE more LLM pass over the
   same section text plus the next :data:`FOLLOW_UP_CHUNKS` chunks of the
   document. Only the previously-missing attributes are merged back; anything
   still missing afterwards stays ``No information available`` and the JSON
   records the attempt (:data:`TOPUP_KEY`) so re-runs never pay for it twice.

2. **A positional fallback on the location ladder**, used when the analyzer's
   own heading/keyword matching finds nothing: the target's share of the
   document's chunks is scanned and the LLM identifies the section content
   inside it. The ratios are expressed against the document's whole **body**
   chunk list — back matter (references, appendices, acknowledgements,
   declarations …) is excluded first, so a review paper whose last third is
   bibliography cannot have its results/conclusion window land in the
   reference list. The reference case of 100 body chunks gives 1-30 for the
   abstract, 10-40 for the introduction and 60-95 for results/conclusion; a
   60-chunk body gives 1-18, 7-24 and 37-57.

Everything target-specific lives in :data:`TARGETS`; adding a fourth target is
a new :class:`TargetSpec` row, not new code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from colorama import Fore, Style

from alr.common.json_utils import get_key_from_file
from alr.common.llm_utils import llm_call
from alr.common.general_utils import clean_response_json_text
from alr.common.sections import (
    ABSTRACT_TEXT_KEY,
    INTRO_TEXT_KEY,
    RESCON_TEXT_KEY,
)
from alr.data_analysis.Data_analysis_system_prompts import (
    Abstract_RP_KEYs_SP,
    Abstrat_identification_SP,
    Intro_RP_KEYs_SP,
    Introduction_identification_SP,
    ResCon_RP_KEYs_SP,
    Results_Conclusion_identification_SP,
)

# The literal the extraction prompts write when an attribute is not present.
MISSING_TOKEN = "no information available"

# Bookkeeping key written into the analysis JSON once a top-up pass has run,
# so an already-completed document is never re-processed for the same gap.
TOPUP_KEY = "Attribute Top-up Attempts"

# How many additional chunks a top-up pass appends to the section text.
FOLLOW_UP_CHUNKS = 3

# Upper bound on the text handed to an identification call, so a positional
# window over a long document cannot blow the model's context.
MAX_IDENTIFY_CHARS = 40000

# Section headings that carry no body content. Positional windows are computed
# over the document with these removed, so a long bibliography cannot push the
# results & conclusion window into the reference list. Matched as whole
# normalized headings (see `is_back_matter`) so body sections that merely start
# with one of these words - "Reference Architecture", "Reference Model" - stay.
BACK_MATTER_LABELS = frozenset({
    "reference", "references", "reference list", "list of references",
    "bibliography", "literature cited", "works cited",
    "acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements",
    "funding", "funding information", "funding statement",
    "conflict of interest", "conflicts of interest",
    "declaration of competing interest", "declarations", "disclosure",
    "author contributions", "author contribution statement",
    "about the authors", "author biographies", "biographies",
    "data availability", "data availability statement",
    "supplementary material", "supplementary materials",
    "supplementary information", "supporting information",
    "ethics statement", "ethical approval",
    "abbreviations", "list of abbreviations", "nomenclature", "glossary",
    "notes", "endnotes", "footnotes",
    "table of contents", "contents", "list of figures", "list of tables",
})

# Headings whose first word alone identifies back matter.
BACK_MATTER_PREFIX_RE = re.compile(r"^(appendix|appendices|annex(e|es)?)\b")


@dataclass(frozen=True)
class TargetSpec:
    """Everything that differs between the three analysis targets."""
    name: str                      # "abstract" | "intro" | "rescon"
    label: str                     # the `type` passed to store_to_json_with_text
    keys: tuple[str, ...]          # attributes the extraction prompt fills
    extract_sp: str                # extraction system prompt
    identify_sp: str               # section-identification system prompt
    error_token: str               # sentinel the identification prompt returns on failure
    window: tuple[float, float]    # positional chunk window, as fractions of the document
    json_attr: str                 # attribute on DataAnalyzeManager holding the JSON path
    text_key: str                  # key under which the identified text is stored


TARGETS: dict[str, TargetSpec] = {
    "abstract": TargetSpec(
        name="abstract",
        label="Abstract",
        keys=(
            "Research Areas",
            "Research Problem",
            "Key Concepts",
            "Objective",
            "Methodology",
            "Results",
            "Conclusion",
        ),
        extract_sp=Abstract_RP_KEYs_SP,
        identify_sp=Abstrat_identification_SP,
        error_token="ERROR_NO_ABSTRACT_FOUND",
        window=(0.0, 0.30),
        json_attr="abstract_json_path",
        text_key=ABSTRACT_TEXT_KEY,
    ),
    "intro": TargetSpec(
        name="intro",
        label="Introduction",
        keys=(
            "Background",
            "Motivation",
            "Gaps & Limitations",
            "RQs & Scope",
        ),
        extract_sp=Intro_RP_KEYs_SP,
        identify_sp=Introduction_identification_SP,
        error_token="ERROR_NO_INTRODUCTION_FOUND",
        window=(0.10, 0.40),
        json_attr="intro_json_path",
        text_key=INTRO_TEXT_KEY,
    ),
    "rescon": TargetSpec(
        name="rescon",
        label="Results & Conclusion",
        keys=(
            "Results Mentioned",
            "Limitations or Boundary Conditions",
            "Summary of the Content",
            "Future Work",
            "Outlook",
        ),
        extract_sp=ResCon_RP_KEYs_SP,
        identify_sp=Results_Conclusion_identification_SP,
        error_token="ERROR_NO_RESULTS_CONCLUSION_FOUND",
        window=(0.60, 0.95),
        json_attr="rescon_json_path",
        text_key=RESCON_TEXT_KEY,
    ),
}


# ---------------------------------------------------------------------------
# Missing-attribute detection
# ---------------------------------------------------------------------------

def is_missing(value) -> bool:
    """True when an extracted attribute holds no usable information."""
    if value is None:
        return True
    if isinstance(value, str):
        return (not value.strip()) or value.strip().lower() == MISSING_TOKEN
    if isinstance(value, (list, tuple)):
        if not value:
            return True
        return all(is_missing(item) for item in value)
    return False


def missing_keys(data: dict, spec: TargetSpec) -> list[str]:
    """The attributes of `spec` that `data` does not (usefully) contain."""
    if not isinstance(data, dict):
        return list(spec.keys)
    return [key for key in spec.keys if key not in data or is_missing(data[key])]


def _load_json(path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return None


def _json_path(MF, spec: TargetSpec):
    return getattr(MF, spec.json_attr, None)


# ---------------------------------------------------------------------------
# Document access
# ---------------------------------------------------------------------------

def load_chunks(MF) -> list[str]:
    """Every chunk of the document, flattened into one document-ordered list."""
    try:
        chunks = get_key_from_file(MF.raw_sec_json_path, "Chunks")
    except Exception:
        return []
    return [str(c) for c in (chunks or [])]


def is_back_matter(section_name) -> bool:
    """
    True for the standard non-body sections that trail (or bracket) a paper:
    references, appendices, acknowledgements, funding, declarations and the
    like.

    Matching is deliberately strict — the normalized heading has to *be* one
    of these labels, not merely contain one — because domain headings such as
    "Reference Architecture" or "Reference Model" are ordinary body sections
    and must not be dropped.
    """
    if not isinstance(section_name, str):
        return False
    # Strip section numbering ("5.", "A.", "IV -") and trailing punctuation.
    low = re.sub(r"^[\s\d\.\)\-–—:]+", "", section_name).strip().lower()
    low = low.strip(" \t:.-–—").strip()
    if not low:
        return False
    if low in BACK_MATTER_LABELS:
        return True
    return bool(BACK_MATTER_PREFIX_RE.match(low))


def load_body_chunks(MF) -> list[str]:
    """
    The document's chunks with the back-matter sections removed, so positional
    ratios are taken over the paper's body rather than over its bibliography.

    A review paper can spend its last 30-40% on references; without this the
    results & conclusion window (60-95%) would land in the reference list.
    Falls back to the full chunk list when filtering leaves nothing — an
    unsectioned document is better served by a rough window than by none.
    """
    try:
        with open(MF.raw_sec_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return load_chunks(MF)

    if not isinstance(data, list):
        return load_chunks(MF)

    body: list[str] = []
    dropped: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Section Name")
        chunks = entry.get("Chunks") or []
        flat = [
            f"{item[0]} {str(item[1]).strip()}"
            if isinstance(item, (list, tuple)) and len(item) >= 2
            else str(item)
            for item in chunks
        ]
        if is_back_matter(name):
            if flat:
                dropped.append(str(name).strip())
            continue
        body.extend(flat)

    if not body:
        return load_chunks(MF)

    if dropped:
        print(f"ℹ Back matter excluded from positional windows: {', '.join(dropped)}")
    return body


def chunk_window(chunks: list[str], spec: TargetSpec) -> tuple[int, int]:
    """
    The positional slice of `chunks` where `spec`'s content usually lives.

    The ratios are taken against the whole list handed in — which is the
    document's **body** (see :func:`load_body_chunks`) — so the reference case
    of 100 body chunks yields 0-30 (abstract), 10-40 (introduction) and 60-95
    (results & conclusion), and any other document is scaled to the same
    proportions. The window is only ever clamped to keep at least one chunk
    inside it.
    """
    n = len(chunks)
    if n == 0:
        return 0, 0
    start = max(0, min(n - 1, int(n * spec.window[0])))
    end = max(start + 1, min(n, int(n * spec.window[1])))
    return start, end


def _clip(text: str) -> str:
    return text if len(text) <= MAX_IDENTIFY_CHARS else text[:MAX_IDENTIFY_CHARS]


# ---------------------------------------------------------------------------
# Positional location fallback
# ---------------------------------------------------------------------------

def identify_via_window(MF, spec: TargetSpec, llm_service) -> tuple[str, list[str]]:
    """Scan the target's share of the document body and identify the content."""
    chunks = load_body_chunks(MF)
    if not chunks:
        return "", []

    start, end = chunk_window(chunks, spec)
    window = chunks[start:end]
    if not window:
        return "", []

    print(
        Fore.BLUE
        + f"\nLocating {spec.label} positionally: body chunks {start + 1}-{end} of {len(chunks)}."
        + Style.RESET_ALL
    )

    user_prompt = f"Text to be assessed:\n {_clip(chr(10).join(window))}"
    try:
        found = llm_call(user_prompt, spec.identify_sp, llm_service)
    except Exception as exc:
        print(f"⚠️ Window identification call failed for {spec.label}: {exc}")
        return "", []

    if not found or spec.error_token in str(found):
        return "", []

    return str(found).strip(), [f"chunk window {start + 1}-{end}"]


def resolve_section_text(MF, target: str, base_text: str = "",
                         provenance: Optional[list[str]] = None) -> tuple[str, list[str]]:
    """
    Return (section text, provenance) for `target`, extending whatever the
    analyzer's own matching already produced.

    `base_text` is returned untouched when it is non-empty, so the cheap
    heading/keyword paths in the analyzers always win over an LLM call.
    """
    spec = TARGETS[target]
    if base_text and str(base_text).strip():
        return str(base_text), list(provenance or [])

    llm_service = getattr(MF, "llm_service", None) or "o"

    text, prov = identify_via_window(MF, spec, llm_service)
    if text:
        return text, prov

    return "", list(provenance or [])


# ---------------------------------------------------------------------------
# Attribute top-up
# ---------------------------------------------------------------------------

def follow_up_text(MF, spec: TargetSpec, section_text: str,
                   count: int = FOLLOW_UP_CHUNKS) -> str:
    """
    The next `count` chunks after the ones the section text came from.

    The section text is matched back onto the body chunk list by looking for
    the last chunk whose body appears in it; when nothing matches (LLM-rewritten
    text, for instance) the chunks just after the target's positional window
    are used instead. Back matter is excluded, so a top-up never pulls the
    bibliography in as "additional context".
    """
    chunks = load_body_chunks(MF)
    if not chunks:
        return ""

    haystack = " ".join(str(section_text).split()).lower()
    last_used = -1
    for idx, chunk in enumerate(chunks):
        body = str(chunk).split(" ", 1)[-1]
        probe = " ".join(body.split()).lower()[:120]
        if len(probe) >= 40 and probe in haystack:
            last_used = idx

    if last_used == -1:
        _, last_used = chunk_window(chunks, spec)
        last_used -= 1

    follow = chunks[last_used + 1: last_used + 1 + count]
    return "\n".join(str(c) for c in follow)


def top_up_attempts(data: dict) -> int:
    try:
        return int(data.get(TOPUP_KEY, 0))
    except (TypeError, ValueError):
        return 0


def needs_top_up(MF, target: str) -> bool:
    """
    True when the stored analysis for `target` still has empty attributes and
    no top-up pass has been spent on it yet.

    This is the cheap check the pipeline runs on already-analyzed documents:
    it reads one JSON file, no LLM call and no re-extraction.
    """
    spec = TARGETS[target]
    data = _load_json(_json_path(MF, spec))
    if data is None:
        return False
    if top_up_attempts(data) >= 1:
        return False
    return bool(missing_keys(data, spec))


def top_up_missing_attributes(MF, target: str, force: bool = False) -> list[str]:
    """
    Run ONE additional extraction pass for the attributes still missing from
    the stored analysis of `target`, widening the input by the next
    :data:`FOLLOW_UP_CHUNKS` chunks.

    Only the previously-missing attributes are merged back, so an attribute the
    first pass got right is never overwritten. Returns the list of attributes
    that the extra pass managed to fill (empty when nothing improved).
    """
    spec = TARGETS[target]
    path = _json_path(MF, spec)
    data = _load_json(path)
    if data is None:
        return []

    gaps = missing_keys(data, spec)
    if not gaps:
        return []

    if not force and top_up_attempts(data) >= 1:
        print(f"⏩ {spec.label}: top-up already attempted; leaving {len(gaps)} attribute(s) empty.")
        return []

    section_text = data.get(spec.text_key) or ""
    extra = follow_up_text(MF, spec, section_text)

    # Record the attempt even if it fails, so re-runs do not repeat it.
    data[TOPUP_KEY] = top_up_attempts(data) + 1

    filled: list[str] = []
    if extra:
        print(
            Fore.YELLOW
            + f"\n↻ {spec.label}: {len(gaps)} attribute(s) missing "
              f"({', '.join(gaps)}); retrying with {FOLLOW_UP_CHUNKS} more chunks."
            + Style.RESET_ALL
        )
        user_prompt = (
            f"{spec.label} text:\n {section_text}\n\n"
            f"Additional context from the following part of the document:\n {extra}"
        )
        llm_service = getattr(MF, "llm_service", None) or "o"
        try:
            raw = llm_call(user_prompt, spec.extract_sp, llm_service)
            retry = json.loads(clean_response_json_text(raw))
        except Exception as exc:
            print(f"⚠️ {spec.label} top-up pass failed: {exc}")
            retry = None

        if isinstance(retry, dict):
            for key in gaps:
                if key in retry and not is_missing(retry[key]):
                    data[key] = retry[key]
                    filled.append(key)
    else:
        print(f"⚠️ {spec.label}: no further chunks available for a top-up pass.")

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        print(f"⚠️ Could not write {spec.label} JSON after top-up: {exc}")
        return []

    if filled:
        print(Fore.GREEN + f"✓ {spec.label} top-up filled: {', '.join(filled)}" + Style.RESET_ALL)
    else:
        still = missing_keys(data, spec)
        if still:
            print(f"ℹ {spec.label}: {', '.join(still)} stay 'No information available'.")

    return filled
