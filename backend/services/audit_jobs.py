import os
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from backend.services.s3_utils import download_guideline

_jobs: Dict[str, "AuditJob"] = {}
_jobs_lock = threading.Lock()


@dataclass
class AuditJob:
    job_id: str
    status: str = "queued"
    phase: str = "queued"
    progress: int = 0
    message: str = ""
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _update(job: AuditJob, **kwargs):
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = time.time()


def get_job(job_id: str) -> Optional[AuditJob]:
    with _jobs_lock:
        return _jobs.get(job_id)


def create_job() -> AuditJob:
    job = AuditJob(job_id=str(uuid4())[:12])
    with _jobs_lock:
        _jobs[job.job_id] = job
        if len(_jobs) > 200:
            oldest = sorted(_jobs.values(), key=lambda j: j.created_at)[:50]
            for old in oldest:
                if old.status in ("completed", "failed"):
                    _jobs.pop(old.job_id, None)
    return job


_audit_lock = threading.Lock()


def run_job_in_background(
    job: AuditJob,
    runner: Callable[[AuditJob], dict],
    on_success: Optional[Callable[[AuditJob, dict], None]] = None,
    on_failure: Optional[Callable[[AuditJob, Exception], None]] = None,
) -> None:
    def _work():
        _update(job, status="running", phase="starting", progress=5, message="Starting audit…")
        print(f"[job:{job.job_id}] worker started", flush=True)
        try:
            # Only one audit at a time (memory). Surface wait so UI is not stuck at "starting".
            if not _audit_lock.acquire(blocking=False):
                _update(
                    job,
                    phase="waiting",
                    progress=5,
                    message="Waiting for another audit to finish…",
                )
                print(f"[job:{job.job_id}] waiting for audit lock", flush=True)
                _audit_lock.acquire()
            try:
                _update(job, phase="starting", progress=5, message="Starting audit…")
                print(f"[job:{job.job_id}] lock acquired; running pipeline", flush=True)
                result = runner(job)
            finally:
                _audit_lock.release()
            if on_success:
                on_success(job, result)
            _update(
                job,
                status="completed",
                phase="done",
                progress=100,
                message="Audit complete",
                result=result,
            )
            print(f"[job:{job.job_id}] completed", flush=True)
        except Exception as exc:
            if on_failure:
                on_failure(job, exc)
            traceback.print_exc()
            _update(
                job,
                status="failed",
                phase="error",
                progress=100,
                message=str(exc),
                error=str(exc),
            )
            print(f"[job:{job.job_id}] failed: {exc}", flush=True)

    threading.Thread(target=_work, daemon=True).start()


def download_guideline_to_temp(filename: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        download_guideline(filename, tmp.name)
        return tmp.name
