"""
alr.data_analysis.section_resolver
==================================

Shared section-location and attribute-completion logic for the three analysis
targets (Abstract, Introduction, Results & Conclusion).

Before this module each ``*_Analyzer.py`` had its own private ladder for
"where is this section in the document?" and simply accepted
``"No information available"`` for every attribute the LLM could not fill.
Two behaviours are added here, once, for all three targets:

1. **Attribute completion**, in two ordered stages (see
   :func:`complete_missing_attributes`). Any attribute still holding
   ``No information available`` after the first extraction pass triggers:

   a. **A plain re-analysis** — the target's ordinary analyzer
      (``analyze_abstract`` / ``analyze_Introduction`` /
      ``analyze_results_conclusion``) is simply run again, so the cheap,
      well-tested heading/keyword location ladder gets a second shot before
      any positional machinery is involved. The attempt is recorded under
      :data:`REANALYSIS_KEY`.
   b. **The window top-up** — only if (a) left gaps behind. ONE more LLM pass
      over the same section text plus the next :data:`FOLLOW_UP_CHUNKS` chunks
      of the document, recorded under :data:`TOPUP_KEY`.

   Both stages merge back ONLY the previously-missing attributes, so a value
   the first pass got right is never overwritten or regressed; anything still
   missing afterwards stays ``No information available``, and the recorded
   attempt counters mean re-runs never pay for either stage twice.

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

import importlib
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

# The three ordinary analyzers (`analyze_abstract`, `analyze_Introduction`,
# `analyze_results_conclusion`) are NOT imported here at module level: the
# analyzers themselves import this module for `resolve_section_text`, so an
# eager import in either direction is a circular import. They are resolved on
# first use by `_load_analyzer`, from the module/function names recorded on
# each TargetSpec.

# The literal the extraction prompts write when an attribute is not present.
MISSING_TOKEN = "no information available"

# Bookkeeping key written into the analysis JSON once a top-up pass has run,
# so an already-completed document is never re-processed for the same gap.
TOPUP_KEY = "Attribute Top-up Attempts"

# Same idea for the cheaper first stage: one plain re-run of the target's own
# analyzer. Kept as a separate counter so a document can have spent its
# re-analysis without having spent its top-up (and the reverse, for documents
# analyzed before this stage existed).
REANALYSIS_KEY = "Attribute Re-analysis Attempts"

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
    analyzer_module: str = ""      # module holding this target's ordinary analyzer
    analyzer_func: str = ""        # its `(ID, MF) -> 'P'|'F'` entry point


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
        analyzer_module="alr.data_analysis.Abstract_Analyzer",
        analyzer_func="analyze_abstract",
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
        analyzer_module="alr.data_analysis.Introduction_Analyzer",
        analyzer_func="analyze_Introduction",
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
        analyzer_module="alr.data_analysis.Results_Conclusion_Analyzer",
        analyzer_func="analyze_results_conclusion",
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


def _write_json(path, data: dict, spec: TargetSpec) -> bool:
    """Write `data` back to `path`, reporting (rather than raising) on failure."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError as exc:
        print(f"⚠️ Could not write {spec.label} JSON: {exc}")
        return False


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
# Stage 1: plain re-analysis with the target's ordinary analyzer
# ---------------------------------------------------------------------------

def _load_analyzer(spec: TargetSpec):
    """
    The target's ordinary `analyze_*(ID, MF) -> 'P'|'F'` entry point.

    Imported on demand rather than at module level: the analyzers import this
    module, so a top-level import here would be circular.
    """
    if not spec.analyzer_module or not spec.analyzer_func:
        return None
    try:
        module = importlib.import_module(spec.analyzer_module)
        return getattr(module, spec.analyzer_func)
    except (ImportError, AttributeError) as exc:
        print(f"⚠️ Analyzer for {spec.label} unavailable ({spec.analyzer_module}): {exc}")
        return None


def _resolve_document_id(MF, spec: TargetSpec, doc_id=None) -> Optional[str]:
    """
    The UUID the analyzers expect as their first argument.

    Prefers an explicit `doc_id`, then the manager's `current_id` (set by
    `update_id_files`), and finally the stem of the target's JSON file, which
    is always named `<uuid>_<Section>.json`.
    """
    if doc_id:
        return str(doc_id)

    current = getattr(MF, "current_id", None)
    if current:
        return str(current)

    path = _json_path(MF, spec)
    if path:
        stem = Path(str(path)).stem
        if "_" in stem:
            return stem.rsplit("_", 1)[0]
    return None


def reanalysis_attempts(data: dict) -> int:
    try:
        return int(data.get(REANALYSIS_KEY, 0))
    except (TypeError, ValueError):
        return 0


def _merge_reanalysis(before: dict, after, spec: TargetSpec) -> tuple[dict, list[str]]:
    """
    Fold a re-analysis result into the stored one, gap-keys only.

    The analyzers write the whole JSON through `store_to_json_with_text`, so a
    straight re-run REPLACES everything the first pass produced. This keeps the
    re-run's values only where the stored one had a gap; every attribute the
    first pass filled survives untouched, and a re-run that came back with the
    identification error token is discarded outright.
    """
    if not isinstance(after, dict) or not after:
        return dict(before), []

    if any(spec.error_token in str(value) for value in after.values()):
        print(f"⚠️ {spec.label}: re-analysis returned {spec.error_token}; keeping the stored analysis.")
        return dict(before), []

    merged = dict(after)
    filled: list[str] = []

    for key in spec.keys:
        old = before.get(key)
        if key in before and not is_missing(old):
            merged[key] = old                       # first pass got it right; never regress
        elif not is_missing(after.get(key)):
            filled.append(key)                      # merged already carries the new value
        elif key in before:
            merged[key] = old                       # both empty; keep the stored literal

    # The stored section text is what every surviving attribute came from, and
    # what the top-up fallback matches back onto the chunk list, so it wins.
    old_text = before.get(spec.text_key)
    if old_text and str(old_text).strip():
        merged[spec.text_key] = old_text

    for book_key in (TOPUP_KEY, REANALYSIS_KEY):
        if book_key in before:
            merged[book_key] = before[book_key]

    return merged, filled


def reanalyze_missing_attributes(MF, target: str, doc_id=None,
                                 force: bool = False) -> list[str]:
    """
    Stage 1 of attribute completion: run the target's ORDINARY analyzer once
    more and merge whatever it manages to fill into the stored analysis.

    This is deliberately the cheap, boring option — the same heading/keyword
    location ladder and the same extraction prompt the document already went
    through — and it runs before any positional window work. Returns the list
    of previously-missing attributes the re-run filled (empty when nothing
    improved), leaving :func:`top_up_missing_attributes` untouched as the
    fallback for whatever is still empty afterwards.
    """
    spec = TARGETS[target]
    path = _json_path(MF, spec)
    before = _load_json(path)
    if before is None:
        return []

    gaps = missing_keys(before, spec)
    if not gaps:
        return []

    if not force and reanalysis_attempts(before) >= 1:
        print(f"⏩ {spec.label}: re-analysis already attempted; leaving it to the top-up pass.")
        return []

    resolved_id = _resolve_document_id(MF, spec, doc_id)
    if not resolved_id:
        print(f"⚠️ {spec.label}: no document ID available; skipping re-analysis.")
        return []

    analyzer = _load_analyzer(spec)
    if analyzer is None:
        return []

    print(
        Fore.YELLOW
        + f"\n↻ {spec.label}: {len(gaps)} attribute(s) missing "
          f"({', '.join(gaps)}); re-running the standard analyzer first."
        + Style.RESET_ALL
    )

    try:
        status = analyzer(resolved_id, MF)
    except Exception as exc:
        print(f"⚠️ {spec.label} re-analysis failed: {exc}")
        status = "F"

    # The analyzer calls `update_id_files`, so re-read the path rather than
    # trusting the one captured above.
    new_path = _json_path(MF, spec)
    if str(new_path) != str(path):
        print(f"⚠️ {spec.label}: re-analysis retargeted the JSON path; restoring the original file.")
        _write_json(path, before, spec)
        return []

    after = _load_json(new_path) if status == "P" else None
    merged, filled = _merge_reanalysis(before, after, spec)

    # Record the attempt even when it fails, so re-runs do not repeat it.
    merged[REANALYSIS_KEY] = reanalysis_attempts(before) + 1

    if not _write_json(new_path, merged, spec):
        return []

    if filled:
        print(Fore.GREEN + f"✓ {spec.label} re-analysis filled: {', '.join(filled)}" + Style.RESET_ALL)
    else:
        print(f"ℹ {spec.label}: re-analysis added nothing; falling back to the top-up pass.")

    return filled


# ---------------------------------------------------------------------------
# Stage 2: attribute top-up (fallback)
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

    if not extra:
        # No chunks to widen into: the document's sections JSON is missing,
        # unreadable, or holds nothing after this section. That is an
        # environmental problem, NOT a spent attempt -- recording it here would
        # burn the document's one top-up on a pass that never ran, and no later
        # run (after the sections are rebuilt) could ever try again. Re-checking
        # costs a file read and no LLM call.
        print(f"⚠️ {spec.label}: no further chunks available for a top-up pass "
              f"(nothing spent; it will be retried once the document's sections are readable).")
        return []

    # Record the attempt even if the pass itself fails, so re-runs do not repeat it.
    data[TOPUP_KEY] = top_up_attempts(data) + 1

    filled: list[str] = []
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


# ---------------------------------------------------------------------------
# Orchestration: re-analysis first, top-up as the fallback
# ---------------------------------------------------------------------------

def needs_completion(MF, target: str) -> bool:
    """
    True when the stored analysis for `target` still has empty attributes AND
    at least one of the two completion stages is still unspent.

    Like :func:`needs_top_up` this is the cheap pipeline check: one JSON read,
    no LLM call and no re-extraction.
    """
    spec = TARGETS[target]
    data = _load_json(_json_path(MF, spec))
    if data is None:
        return False
    if not missing_keys(data, spec):
        return False
    return reanalysis_attempts(data) < 1 or top_up_attempts(data) < 1


def _space_manager(MF):
    from alr.common.file_manager import DataAnalyzeManager
    return MF if isinstance(MF, DataAnalyzeManager) else DataAnalyzeManager(str(MF))


def _space_documents(MF):
    """``[(uuid, filename), ...]`` from the space's registry."""
    from alr.common.excel_utils import read_excel_cached

    registry = Path(MF.excel_success)
    if not registry.exists():
        return []
    try:
        df = read_excel_cached(registry)
    except Exception as exc:
        print(f"⚠️ Could not read the registry {registry}: {exc}")
        return []
    if "UUID" not in df.columns:
        return []
    names = df["filename"] if "filename" in df.columns else None
    out = []
    for i, uuid in enumerate(df["UUID"].tolist()):
        uuid = str(uuid).strip()
        if not uuid or uuid.lower() == "nan":
            continue
        out.append((uuid, str(names.iloc[i]) if names is not None else ""))
    return out


def scan_space(MF, targets=None) -> dict:
    """
    Report which documents of a storage space still have empty attributes and
    could be completed, **without doing any work**.

    Returns ``{target: {"documents": n, "eligible": [uuid, ...],
    "attributes": n, "spent": [uuid, ...]}}`` where *eligible* are the
    documents with a gap AND an unspent completion stage (what
    :func:`complete_space` would act on) and *spent* are those with a gap whose
    stages are both used up. Reads one JSON per document per target: no LLM
    call, no PDF, no re-extraction -- so it can be shown to the user as a cost
    estimate before they commit.
    """
    MF = _space_manager(MF)
    targets = list(targets) if targets else list(TARGETS)
    report = {t: {"documents": 0, "eligible": [], "attributes": 0, "spent": []}
              for t in targets}

    for uuid, _name in _space_documents(MF):
        MF.update_id_files(uuid)
        for target in targets:
            spec = TARGETS[target]
            data = _load_json(_json_path(MF, spec))
            if data is None:
                continue                      # not analyzed for this target
            report[target]["documents"] += 1
            gaps = missing_keys(data, spec)
            if not gaps:
                continue
            if reanalysis_attempts(data) < 1 or top_up_attempts(data) < 1:
                report[target]["eligible"].append(uuid)
                report[target]["attributes"] += len(gaps)
            else:
                report[target]["spent"].append(uuid)
    return report


def complete_space(MF, targets=None, force: bool = False, should_cancel=None,
                   progress_callback=None, push_sql: bool = True, db_path=None) -> dict:
    """
    Complete the missing attributes of a whole storage space **from what the
    space already holds** -- the per-document analysis JSONs and the raw
    sections JSON. No PDF file or input folder is needed and nothing is
    re-extracted: this is the completion ladder of
    :func:`complete_missing_attributes` applied document by document.

    A document is touched only when it has a gap and an unspent stage, so a
    space that is already complete costs one JSON read per document per target.
    Whatever gets filled is written back to the analysis JSON, the per-attribute
    A/NA log is refreshed, and (unless ``push_sql`` is False) the document is
    pushed into the review database immediately -- so a cancelled or crashed
    run keeps everything it had already resolved.

    Returns ``{"documents": n, "filled": {uuid: {target: [keys]}},
    "attributes": n, "synced": n, "failed": [(uuid, error)]}``.
    """
    MF = _space_manager(MF)
    targets = list(targets) if targets else list(TARGETS)
    docs = _space_documents(MF)

    result = {"documents": len(docs), "filled": {}, "attributes": 0,
              "synced": 0, "failed": []}
    if not docs:
        print("⚠️ No analyzed documents found in this storage space.")
        return result

    for i, (uuid, name) in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print("Attribute resolution cancelled by user.")
            break
        if progress_callback:
            progress_callback(i, len(docs), name or uuid)

        MF.update_id_files(uuid)
        filled_here = {}
        for target in targets:
            if should_cancel is not None and should_cancel():
                break
            try:
                if not force and not needs_completion(MF, target):
                    continue
                filled = complete_missing_attributes(MF, target, doc_id=uuid, force=force)
                if filled:
                    filled_here[target] = filled
                    _refresh_target_log(MF, target, uuid)
            except Exception as exc:  # noqa: BLE001 - one document must not end the pass
                print(f"⚠️ {TARGETS[target].label} completion failed for {name or uuid}: {exc}")
                result["failed"].append((uuid, f"{target}: {exc}"))

        if not filled_here:
            continue

        result["filled"][uuid] = filled_here
        result["attributes"] += sum(len(v) for v in filled_here.values())
        print(Fore.GREEN + f"✓ {name or uuid}: filled "
              + "; ".join(f"{TARGETS[t].label} → {', '.join(k)}" for t, k in filled_here.items())
              + Style.RESET_ALL)

        # Push straight away: the newly resolved values are of no use sitting in
        # the JSON, and a per-document push means a cancel keeps what is done.
        if push_sql:
            try:
                from alr.common.sql_store import sync_one_document, DB_PATH
                if sync_one_document(MF, name, db_path=db_path or DB_PATH):
                    result["synced"] += 1
                else:
                    print(f"ℹ No registry row matched '{name}'; it will be picked up by a full sync.")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Could not sync {name or uuid} into the review database: {exc}")

    return result


def _refresh_target_log(MF, target, uuid):
    """Rewrite the per-attribute A/NA log row after a completion changed the
    JSON. Imported lazily: the processor imports this module."""
    try:
        from alr.data_analysis.Pdf_File_processor import _refresh_attribute_log
        _refresh_attribute_log(MF, target, uuid)
    except Exception as exc:  # noqa: BLE001 - the JSON is the source of truth
        print(f"⚠️ Could not refresh the {target} attribute log: {exc}")


def complete_missing_attributes(MF, target: str, doc_id=None,
                                force: bool = False) -> list[str]:
    """
    Fill the attributes still missing from the stored analysis of `target`.

    Two stages, in order, each of which only ever merges back attributes that
    were missing beforehand:

    1. :func:`reanalyze_missing_attributes` — re-run the target's ordinary
       analyzer. Cheap, uses the same heading/keyword ladder the document
       already went through, and needs no positional machinery.
    2. :func:`top_up_missing_attributes` — the widened window pass, run ONLY
       for whatever stage 1 could not fill.

    Returns every attribute filled across both stages, in the order they were
    filled. This is the entry point the pipeline should call; the two stage
    functions remain individually callable and behave exactly as before.
    """
    spec = TARGETS[target]
    data = _load_json(_json_path(MF, spec))
    if data is None:
        return []

    if not missing_keys(data, spec):
        return []

    filled = list(reanalyze_missing_attributes(MF, target, doc_id=doc_id, force=force))

    # Re-read: stage 1 rewrote the file, and may have closed every gap.
    data = _load_json(_json_path(MF, spec)) or data
    if not missing_keys(data, spec):
        return filled

    for key in top_up_missing_attributes(MF, target, force=force):
        if key not in filled:
            filled.append(key)

    return filled