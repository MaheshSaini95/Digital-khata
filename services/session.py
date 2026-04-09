"""
services/session.py - In-memory session store with TTL
Falls back to a simple dict; swap for Redis in production.
"""
from __future__ import annotations
import time
import threading
import logging
from typing import Any, Optional
from config import Config

logger = logging.getLogger(__name__)

# Thread-safe in-process store: { whatsapp_number -> session_dict }
_store: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _ttl_seconds() -> float:
    return Config.SESSION_TTL_MINUTES * 60


# ─── Public API ───────────────────────────────────────────

def get_session(number: str) -> dict:
    """Return session for number, creating a fresh one if absent or expired."""
    with _lock:
        s = _store.get(number)
        if s and (_now() - s["_touched"]) < _ttl_seconds():
            s["_touched"] = _now()
            return s
        # New / expired
        fresh = _new_session(number)
        _store[number] = fresh
        return fresh


def set_session(number: str, **kwargs) -> None:
    """Update one or more fields on an existing session."""
    with _lock:
        s = _store.setdefault(number, _new_session(number))
        s.update(kwargs)
        s["_touched"] = _now()


def clear_session(number: str) -> None:
    """Reset a session back to idle state."""
    with _lock:
        _store[number] = _new_session(number)


def _new_session(number: str) -> dict:
    return {
        "number": number,
        "state": "idle",           # FSM state
        "client_id": None,
        "customer_name": None,
        "items": [],
        "current_total": 0.0,
        "payment": 0.0,
        "record_id": None,         # for updates
        "_touched": _now(),
    }


# ─── Background cleanup ───────────────────────────────────

def _cleanup_expired():
    """Purge expired sessions (called by background thread)."""
    with _lock:
        expired = [
            k for k, v in _store.items()
            if (_now() - v["_touched"]) >= _ttl_seconds()
        ]
        for k in expired:
            del _store[k]
    logger.debug(f"Session cleanup: removed {len(expired)} expired sessions")


def start_cleanup_thread():
    """Start a daemon thread that cleans up stale sessions every 5 minutes."""
    def run():
        while True:
            time.sleep(300)
            _cleanup_expired()

    t = threading.Thread(target=run, daemon=True)
    t.start()
