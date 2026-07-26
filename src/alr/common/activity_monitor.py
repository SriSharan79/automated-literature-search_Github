"""
alr.common.activity_monitor
===========================

A tiny, thread-safe registry of **live model activity** so the desktop UI can
always show what the backend is doing: which chat/embedding model is being
called right now, how many tokens have been processed by text-generation models,
and how many vectors have been embedded.

The backend (``llm_utils``) calls :func:`begin`/:func:`end` around each call and
:func:`record_chat`/:func:`record_embedding` on completion; the UI polls
:func:`snapshot` on a timer. Every record also appends a line to a per-day
backend log (``00_LLM_Log_Data/<date>_model_activity.log``).

Nothing here imports Tk or ``llm_utils`` (``llm_utils`` imports this), so it is
safe to use from any worker thread.
"""

import os
import threading
import time
from datetime import datetime

_LOCK = threading.RLock()


def _blank_chat():
    return {"calls": 0, "in_tokens": 0, "out_tokens": 0,
            "last_model": None, "last_service": None, "last_ts": None}


def _blank_embed():
    return {"calls": 0, "vectors": 0, "last_model": None,
            "last_service": None, "last_dim": 0, "last_ts": None}


_STATE = {
    "active": None,          # {"kind","service","model","since"} while a call runs
    "chat": _blank_chat(),
    "embed": _blank_embed(),
}


def _coerce_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Backend log
# ---------------------------------------------------------------------------
def _log_path():
    from alr.common.file_manager import ALR_main_folder  # lazy: avoid import cycles
    d = os.path.join(str(ALR_main_folder), "00_LLM_Log_Data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{datetime.now():%Y-%m-%d}_model_activity.log")


def _append_log(line: str):
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # logging must never break a model call


# ---------------------------------------------------------------------------
# Active (in-flight) state
# ---------------------------------------------------------------------------
def begin(kind: str, service=None, model=None):
    """Mark a call as running (``kind`` is 'chat' or 'embed')."""
    with _LOCK:
        _STATE["active"] = {"kind": kind, "service": service,
                            "model": model, "since": time.time()}


def end():
    """Clear the in-flight marker."""
    with _LOCK:
        _STATE["active"] = None


class active:
    """Context manager: ``with active('embed', service, model): ...``."""

    def __init__(self, kind, service=None, model=None):
        self._args = (kind, service, model)

    def __enter__(self):
        begin(*self._args)
        return self

    def __exit__(self, *exc):
        end()
        return False


# ---------------------------------------------------------------------------
# Completion records
# ---------------------------------------------------------------------------
def record_chat(service, model, in_tokens, out_tokens, time_taken=None):
    it, ot = _coerce_int(in_tokens), _coerce_int(out_tokens)
    with _LOCK:
        c = _STATE["chat"]
        c["calls"] += 1
        c["in_tokens"] += it
        c["out_tokens"] += ot
        c["last_model"], c["last_service"], c["last_ts"] = model, service, time.time()
    _append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S}\tCHAT\tservice={service}\t"
                f"model={model}\tin_tokens={in_tokens}\tout_tokens={out_tokens}\t"
                f"time_taken={time_taken}")


def record_embedding(service, model, n_vectors, vector_dim=0, time_taken=None):
    n = _coerce_int(n_vectors)
    with _LOCK:
        e = _STATE["embed"]
        e["calls"] += 1
        e["vectors"] += n
        e["last_model"], e["last_service"] = model, service
        e["last_dim"], e["last_ts"] = _coerce_int(vector_dim), time.time()
    _append_log(f"{datetime.now():%Y-%m-%d %H:%M:%S}\tEMBED\tservice={service}\t"
                f"model={model}\tvectors={n_vectors}\tdim={vector_dim}\t"
                f"time_taken={time_taken}")


# ---------------------------------------------------------------------------
# Read / reset
# ---------------------------------------------------------------------------
def snapshot() -> dict:
    """A copy of the current activity state (safe to read from the UI thread)."""
    with _LOCK:
        return {
            "active": dict(_STATE["active"]) if _STATE["active"] else None,
            "chat": dict(_STATE["chat"]),
            "embed": dict(_STATE["embed"]),
        }


def reset():
    """Zero the counters (e.g. the user cleared the session)."""
    with _LOCK:
        _STATE["active"] = None
        _STATE["chat"] = _blank_chat()
        _STATE["embed"] = _blank_embed()
