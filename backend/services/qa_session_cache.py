"""Durable follow-up QA session cache.

Ask-a-question used an in-memory dict only, so any backend restart (deploy,
OOM, systemd bounce) made clients see "Session expired" even with a valid
session_id. This module keeps a process-local memory cache and a disk snapshot
of the serializable fields, then rebuilds FAISS guideline stores on demand.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.config import ROOT_DIR, env

logger = logging.getLogger("medical_auditor.qa_session_cache")

GuidelineStore = Tuple[str, Any, List[str]]

_LOCK = threading.RLock()
_MEMORY: Dict[str, dict] = {}

TTL_SECONDS = int(env("QA_SESSION_TTL_HOURS", "48") or "48") * 3600
MAX_DISK_SESSIONS = int(env("QA_SESSION_MAX_DISK", "200") or "200")
DISK_DIR = os.path.join(
    str(ROOT_DIR),
    env("QA_SESSION_DIR", "data/qa_sessions") or "data/qa_sessions",
)


def _path(session_id: str) -> str:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return os.path.join(DISK_DIR, f"{safe}.json")


def _prune_disk(now: Optional[float] = None) -> None:
    now = now or time.time()
    try:
        os.makedirs(DISK_DIR, exist_ok=True)
        entries: List[Tuple[float, str]] = []
        for name in os.listdir(DISK_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(DISK_DIR, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if now - mtime > TTL_SECONDS:
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            entries.append((mtime, path))
        if len(entries) <= MAX_DISK_SESSIONS:
            return
        entries.sort()  # oldest first
        for _, path in entries[: max(0, len(entries) - MAX_DISK_SESSIONS)]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError as exc:
        logger.warning("QA session disk prune failed: %s", exc)


def _serialize_for_disk(payload: dict) -> dict:
    """Persist only JSON-safe fields; FAISS indexes are rebuilt on load."""
    return {
        "case_text": payload.get("case_text") or "",
        "guidelines": list(payload.get("guidelines") or []),
        "guideline": payload.get("guideline") or "",
        "created_at": payload.get("created_at") or time.time(),
        # Image blobs are large and already transcribed into case_text; skip.
        "images": [],
    }


def _write_disk(session_id: str, payload: dict) -> None:
    try:
        os.makedirs(DISK_DIR, exist_ok=True)
        path = _path(session_id)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_serialize_for_disk(payload), fh)
        os.replace(tmp, path)
        _prune_disk()
    except OSError as exc:
        logger.warning("QA session disk save failed for %s: %s", session_id, exc)


def _read_disk(session_id: str) -> Optional[dict]:
    path = _path(session_id)
    if not os.path.isfile(path):
        return None
    try:
        if time.time() - os.path.getmtime(path) > TTL_SECONDS:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not data.get("case_text"):
            return None
        data.setdefault("images", [])
        data.setdefault("guidelines", [])
        data.setdefault("guideline", "")
        data.setdefault("guideline_stores", [])
        return data
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("QA session disk load failed for %s: %s", session_id, exc)
        return None


def rebuild_guideline_stores(guidelines: List[str]) -> List[GuidelineStore]:
    """Rebuild FAISS stores from guideline filenames (uses .rag_cache when warm)."""
    from backend.rag.rag_manager import get_or_create_index

    stores: List[GuidelineStore] = []
    for name in guidelines or []:
        key = str(name or "").strip()
        if not key:
            continue
        try:
            index, chunks = get_or_create_index(key, cache_key=key)
            stores.append((key, index, chunks))
        except Exception as exc:
            logger.warning("Failed to rebuild guideline store for %s: %s", key, exc)
    return stores


def put(session_id: str, payload: dict) -> None:
    """Store a QA session in memory and on disk."""
    if not session_id:
        return
    entry = dict(payload)
    entry["created_at"] = time.time()
    with _LOCK:
        _MEMORY[session_id] = entry
        _write_disk(session_id, entry)


def get(session_id: str) -> Optional[dict]:
    """Return session payload with guideline_stores ready for QA retrieval."""
    if not session_id:
        return None

    with _LOCK:
        entry = _MEMORY.get(session_id)
        if entry is None:
            entry = _read_disk(session_id)
            if entry is None:
                return None
            _MEMORY[session_id] = entry
        else:
            created = float(entry.get("created_at") or 0)
            if created and time.time() - created > TTL_SECONDS:
                _MEMORY.pop(session_id, None)
                try:
                    os.remove(_path(session_id))
                except OSError:
                    pass
                return None

        stores = entry.get("guideline_stores") or []
        if not stores and entry.get("index") is not None:
            stores = [(entry.get("guideline") or "", entry["index"], entry["chunks"])]
            entry["guideline_stores"] = stores

        if not stores:
            names = list(entry.get("guidelines") or [])
            if not names and entry.get("guideline"):
                names = [str(entry["guideline"])]
            stores = rebuild_guideline_stores(names)
            entry["guideline_stores"] = stores
            # Keep rebuilt stores in memory only (not disk).
            _MEMORY[session_id] = entry

        return entry


def clear_memory() -> None:
    """Test helper."""
    with _LOCK:
        _MEMORY.clear()
