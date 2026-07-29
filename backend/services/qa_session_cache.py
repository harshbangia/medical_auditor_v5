"""Durable follow-up QA session cache.

Ask-a-question needs the extracted case text + guideline names after the audit
finishes. Reviewers often Ask 30–90+ minutes later; in-memory (and even local
disk) entries disappear when the API process restarts or the EC2 disk fills.

Lookup order: memory → local disk → PostgreSQL.
Persist order: memory + disk + PostgreSQL.
Default TTL: 7 days (QA_SESSION_TTL_HOURS).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.config import ROOT_DIR, env

logger = logging.getLogger("medical_auditor.qa_session_cache")

GuidelineStore = Tuple[str, Any, List[str]]

_LOCK = threading.RLock()
_MEMORY: Dict[str, dict] = {}

# 7 days — long enough for manual report review + multi-day reopen from history.
TTL_SECONDS = int(env("QA_SESSION_TTL_HOURS", "168") or "168") * 3600
MAX_DISK_SESSIONS = int(env("QA_SESSION_MAX_DISK", "500") or "500")
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


def _serialize_payload(payload: dict) -> dict:
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
            json.dump(_serialize_payload(payload), fh)
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


def _write_db(session_id: str, payload: dict) -> None:
    """Primary durable store — survives API restarts and local disk cleanup."""
    try:
        from backend.db.database import SessionLocal
        from backend.db.models import QaSession

        serial = _serialize_payload(payload)
        db = SessionLocal()
        try:
            row = db.query(QaSession).filter(QaSession.session_id == session_id).first()
            now = datetime.utcnow()
            if row is None:
                row = QaSession(session_id=session_id, created_at=now)
                db.add(row)
            row.case_text = serial["case_text"]
            row.guidelines_json = json.dumps(serial["guidelines"])
            row.guideline = serial["guideline"] or None
            row.updated_at = now
            # Opportunistic prune of expired rows
            cutoff = now - timedelta(seconds=TTL_SECONDS)
            db.query(QaSession).filter(QaSession.created_at < cutoff).delete(
                synchronize_session=False
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as exc:
        logger.warning("QA session DB save failed for %s: %s", session_id, exc)


def _read_db(session_id: str) -> Optional[dict]:
    try:
        from backend.db.database import SessionLocal
        from backend.db.models import QaSession

        db = SessionLocal()
        try:
            row = db.query(QaSession).filter(QaSession.session_id == session_id).first()
            if row is None or not row.case_text:
                return None
            created = row.created_at or row.updated_at
            if created and datetime.utcnow() - created > timedelta(seconds=TTL_SECONDS):
                db.delete(row)
                db.commit()
                return None
            try:
                guidelines = json.loads(row.guidelines_json or "[]")
            except json.JSONDecodeError:
                guidelines = []
            if not isinstance(guidelines, list):
                guidelines = []
            return {
                "case_text": row.case_text,
                "guidelines": guidelines,
                "guideline": row.guideline or "",
                "images": [],
                "guideline_stores": [],
                "created_at": created.timestamp() if created else time.time(),
            }
        finally:
            db.close()
    except Exception as exc:
        logger.warning("QA session DB load failed for %s: %s", session_id, exc)
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


def _ensure_stores(entry: dict) -> dict:
    stores = entry.get("guideline_stores") or []
    if not stores and entry.get("index") is not None:
        stores = [(entry.get("guideline") or "", entry["index"], entry["chunks"])]
        entry["guideline_stores"] = stores

    if not stores:
        names = list(entry.get("guidelines") or [])
        if not names and entry.get("guideline"):
            names = [str(entry["guideline"])]
        # Rebuild outside the caller’s critical path when possible; still sync here.
        stores = rebuild_guideline_stores(names)
        entry["guideline_stores"] = stores
    return entry


def put(session_id: str, payload: dict) -> None:
    """Store a QA session in memory, disk, and PostgreSQL."""
    if not session_id:
        return
    entry = dict(payload)
    entry["created_at"] = time.time()
    with _LOCK:
        _MEMORY[session_id] = entry
        _write_disk(session_id, entry)
    # DB write outside memory lock (can be slower).
    _write_db(session_id, entry)


def get(session_id: str) -> Optional[dict]:
    """Return session payload with guideline_stores ready for QA retrieval."""
    if not session_id:
        return None

    entry: Optional[dict] = None
    with _LOCK:
        entry = _MEMORY.get(session_id)
        if entry is not None:
            created = float(entry.get("created_at") or 0)
            if created and time.time() - created > TTL_SECONDS:
                _MEMORY.pop(session_id, None)
                try:
                    os.remove(_path(session_id))
                except OSError:
                    pass
                entry = None
        if entry is None:
            entry = _read_disk(session_id)
            if entry is not None:
                _MEMORY[session_id] = entry

    if entry is None:
        entry = _read_db(session_id)
        if entry is None:
            return None
        with _LOCK:
            _MEMORY[session_id] = entry
            _write_disk(session_id, entry)

    # FAISS rebuild can be slow — do not hold _LOCK.
    entry = _ensure_stores(entry)
    with _LOCK:
        _MEMORY[session_id] = entry
    return entry


def clear_memory() -> None:
    """Test helper."""
    with _LOCK:
        _MEMORY.clear()
