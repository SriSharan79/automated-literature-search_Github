"""
alr.analysis_evaluation.word_grounding
======================================

Word-level grounding for the substring/grounding evaluation.

The original check (``data_evaluator._is_subset_match``) asked one binary
question per extracted value: *is this whole string a literal substring of the
reference text?* That is the right question for the short term lists
(``Research Areas`` / ``Key Concepts``, see
:data:`alr.common.sections.LITERAL_MATCH_KEYS`), whose values are copied
verbatim out of the abstract. It is the wrong question for the prose
attributes (Research Problem, Objective, Methodology, Conclusion, Results, and
every introduction / results-&-conclusion key): the analyzer paraphrases and
re-orders, so a sentence that is entirely built from the abstract's own words
still scores a flat ``False`` and the document looks ungrounded.

This module answers the graded question instead. One extracted value is:

1. stripped of its **articles and prepositions** (:data:`STOPWORDS` — plus the
   coordinating conjunctions and the copulas, which carry no content either),
2. split into its remaining content words, and
3. each word checked against the reference text
   (``ABSTRACT_TEXT_KEY`` / ``INTRO_TEXT_KEY`` / ``RESCON_TEXT_KEY``).

The result is a per-word True/False tally and the percentage of the value's
content words that are present in the reference — its *grounding*. Words are
compared as **whole tokens** on both sides, so "art" does not ground itself in
"artificial"; the comparison is case-insensitive and punctuation-insensitive.

The tally is what the evaluation now stores: the same ``true``/``false``
counters as before, only counting words rather than whole values, so the
existing per-section stats, the overview workbooks and the SQL evaluation
score all keep their shape and simply become graded.
"""

from __future__ import annotations

import re

# Articles and prepositions — what the user asked to drop before matching —
# plus the coordinating conjunctions ("and", "or", "but") and the copulas
# ("is", "are", "was", …). Those are function words exactly like the
# prepositions: keeping them would inflate every value's grounding, because
# "is" and "and" appear in essentially every reference text and would be
# counted as evidence. Nothing here carries content, so nothing here is
# evidence either way.
STOPWORDS = frozenset("""
a an the
about above across after against along amid among around as at
before behind below beneath beside besides between beyond but by
concerning considering despite down during
except excepting excluding
following for from
in inside into
like
near
of off on onto opposite out outside over
past per
regarding round
since
than through throughout till to toward towards
under underneath unlike until up upon
versus via
with within without
and or nor so yet
is are was were be been being am
that this these those which who whom whose
it its their there here
""".split())

# A word for grounding purposes: letters/digits, optionally carrying internal
# hyphens or apostrophes ("model-based", "system's"). Everything else is a
# separator, so punctuation never welds itself onto a token and never blocks a
# match.
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)


def tokenize(text) -> list[str]:
    """Every word of ``text``, lower-cased, in order (stopwords included)."""
    return [m.group(0).lower() for m in _WORD.finditer(str(text or ""))]


def content_words(text) -> list[str]:
    """
    The words of ``text`` that carry content: :func:`tokenize` minus
    :data:`STOPWORDS`, in their original order, **duplicates kept**.

    Duplicates are deliberate: a value that repeats a word is longer, and its
    grounding percentage should reflect the value as written rather than a
    de-duplicated idealisation of it. (Repeats ground or fail together, so this
    changes the weight of a value, never its verdict.)
    """
    return [w for w in tokenize(text) if w not in STOPWORDS]


def reference_words(reference) -> frozenset:
    """The set of whole words present in a reference text, for lookup."""
    return frozenset(tokenize(reference))


def grounding(value, reference, vocabulary=None) -> dict:
    """
    Word-level grounding of one extracted ``value`` against ``reference``.

    ``vocabulary`` is an optional pre-computed :func:`reference_words` set — the
    callers evaluate many values against the same reference text and pass it in
    so the reference is tokenized once per document instead of once per value.

    Returns::

        {"words": [(word, True/False), …],   # in order, duplicates kept
         "checked": n,                        # content words examined
         "true": n_found, "false": n_missing,
         "percent": 0.0-100.0 or None,        # None when there is nothing to check
         "missing": [word, …],                # unique, in order of appearance
         "grounded": True/False}              # every content word present

    A value with no content words at all (empty, "Not Found", or nothing but
    articles and prepositions) yields ``checked == 0`` and ``percent is None``:
    it contributes nothing to the score in either direction, because there is
    no evidence to weigh. ``grounded`` is False for such a value — an empty
    answer is not a grounded one.
    """
    vocab = reference_words(reference) if vocabulary is None else vocabulary
    words = content_words(value)
    flags = [(w, w in vocab) for w in words]
    found = sum(1 for _w, ok in flags if ok)
    checked = len(flags)
    missing = list(dict.fromkeys(w for w, ok in flags if not ok))
    return {
        "words": flags,
        "checked": checked,
        "true": found,
        "false": checked - found,
        "percent": round(100.0 * found / checked, 1) if checked else None,
        "missing": missing,
        "grounded": bool(checked) and found == checked,
    }


def missing_preview(result, limit=12) -> str:
    """The ungrounded words of a :func:`grounding` result as one cell value."""
    missing = result.get("missing") or []
    shown = ", ".join(missing[:limit])
    return shown + (f", … (+{len(missing) - limit})" if len(missing) > limit else "")
