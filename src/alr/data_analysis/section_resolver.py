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

2. **Two extra rungs on the location ladder**, used when the analyzer's own
   heading/keyword matching finds nothing:

   * ask the LLM which of the document's *headings* most likely carry the
     target content, then feed those sections' text to the extractor;
   * failing that, fall back to a positional chunk window (a 100-chunk paper
     yields chunks 1-30 for the abstract, 10-40 for the introduction and
     60-95 for results/conclusion) and let the LLM identify the content
     inside that window.

Everything target-specific lives in :data:`TARGETS`; adding a fourth target is
a new :class:`TargetSpec` row, not new code.
"""

from __future__ import annotations

import json
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
    Heading_section_selection_SP,
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


@dataclass(frozen=True)
class TargetSpec:
    """Everything that differs between the three analysis targets."""
    name: str                      # "abstract" | "intro" | "rescon"
    label: str                     # the `type` passed to store_to_json_with_text
    description: str               # plain-English target, used in the heading prompt
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
        description=(
            "the abstract of the publication - the short standalone summary "
            "printed before the introduction"
        ),
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
        description=(
            "the introduction of the publication - background, motivation, "
            "gaps in previous work and the research questions or scope"
        ),
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
        description=(
            "the results and conclusion content of the publication - findings, "
            "discussion, summary, limitations, future work and outlook"
        ),
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


def load_headings(MF) -> list[str]:
    """
    The document's section headings, in document order.

    Primary source is the sectioned JSON ("Section Name"); the Docling
    heading list cached alongside the chunks is used to fill in when the
    sectioning produced almost nothing.
    """
    headings: list[str] = []

    data = None
    try:
        with open(MF.raw_sec_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        data = None

    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("Section Name")
                if isinstance(name, str) and name.strip():
                    headings.append(name.strip())

    if len(headings) >= 3:
        return headings

    cached = _load_json(getattr(MF, "raw_chunks_json_path", None))
    if cached:
        for item in cached.get("headings", []) or []:
            title = item.get("title") if isinstance(item, dict) else None
            if isinstance(title, str) and title.strip() and title.strip() not in headings:
                headings.append(title.strip())

    return headings


def section_texts_for(MF, wanted: list[str]) -> list[tuple[str, str]]:
    """(section name, text) for every section whose heading matches `wanted`."""
    out: list[tuple[str, str]] = []
    if not wanted:
        return out

    wanted_lower = [w.strip().lower() for w in wanted if isinstance(w, str) and w.strip()]
    if not wanted_lower:
        return out

    try:
        with open(MF.raw_sec_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return out

    if not isinstance(data, list):
        return out

    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Section Name")
        text = entry.get("Text_Content")
        if not isinstance(name, str) or not isinstance(text, str) or not text.strip():
            continue
        low = name.strip().lower()
        if any(w == low or w in low or low in w for w in wanted_lower):
            out.append((name.strip(), text.strip()))

    return out


def chunk_window(chunks: list[str], spec: TargetSpec) -> tuple[int, int]:
    """
    The positional slice of `chunks` where `spec`'s content usually lives.

    For a 100-chunk document this yields 0-30 (abstract), 10-40 (introduction)
    and 60-95 (results & conclusion). Short documents get a floor of five
    chunks so the window never collapses to nothing.
    """
    n = len(chunks)
    if n == 0:
        return 0, 0
    start = max(0, min(n - 1, int(n * spec.window[0])))
    end = max(start + 1, min(n, int(n * spec.window[1])))
    if end - start < 5:
        end = min(n, start + 5)
    return start, end


def _clip(text: str) -> str:
    return text if len(text) <= MAX_IDENTIFY_CHARS else text[:MAX_IDENTIFY_CHARS]


# ---------------------------------------------------------------------------
# Location ladder rungs
# ---------------------------------------------------------------------------

def select_headings_via_llm(MF, spec: TargetSpec, llm_service) -> list[str]:
    """Ask the LLM which of the document's headings carry `spec`'s content."""
    headings = load_headings(MF)
    if not headings:
        return []

    user_prompt = (
        f"Target section: {spec.description}\n\n"
        f"Headings:\n{json.dumps(headings, ensure_ascii=False, indent=1)}"
    )
    try:
        raw = llm_call(user_prompt, Heading_section_selection_SP, llm_service)
    except Exception as exc:
        print(f"⚠️ Heading selection call failed for {spec.label}: {exc}")
        return []

    if not raw:
        return []

    text = str(raw).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        selected = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []

    if not isinstance(selected, list):
        return []
    return [str(h).strip() for h in selected if str(h).strip()]


def identify_via_headings(MF, spec: TargetSpec, llm_service) -> tuple[str, list[str]]:
    """Rung 1: LLM-selected headings -> that section text, verbatim."""
    selected = select_headings_via_llm(MF, spec, llm_service)
    if not selected:
        return "", []

    matches = section_texts_for(MF, selected)
    if not matches:
        return "", []

    names = [name for name, _ in matches]
    combined = "\n\n".join(f"{name}\n{text}" for name, text in matches)
    print(Fore.BLUE + f"\nHeadings selected for {spec.label}: {', '.join(names)}" + Style.RESET_ALL)
    return combined, [f"heading match: {name}" for name in names]


def identify_via_window(MF, spec: TargetSpec, llm_service) -> tuple[str, list[str]]:
    """Rung 2: positional chunk window, then LLM extraction of the content."""
    chunks = load_chunks(MF)
    if not chunks:
        return "", []

    start, end = chunk_window(chunks, spec)
    window = chunks[start:end]
    if not window:
        return "", []

    print(
        Fore.BLUE
        + f"\nNo headings matched {spec.label}; scanning chunks {start + 1}-{end} of {len(chunks)}."
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

    text, prov = identify_via_headings(MF, spec, llm_service)
    if text:
        return text, prov

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

    The section text is matched back onto the chunk list by looking for the
    last chunk whose body appears in it; when nothing matches (LLM-rewritten
    text, for instance) the chunks just after the target's positional window
    are used instead.
    """
    chunks = load_chunks(MF)
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
