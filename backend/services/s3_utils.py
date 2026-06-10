import logging
import os
import time
from typing import List, Optional

import boto3
from botocore.config import Config

logger = logging.getLogger("medical_auditor.s3")

BUCKET_NAME = os.getenv("S3_BUCKET", "glowix-medical-auditor")
GUIDELINES_PREFIX = "guidelines/"

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            config=Config(
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )
    return _s3_client


def list_guideline_pdfs() -> List[str]:
    """List all PDF guideline filenames from S3 (paginated)."""
    client = get_s3_client()
    names: List[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=GUIDELINES_PREFIX):
        for obj in page.get("Contents") or []:
            key = obj.get("Key") or ""
            if not key or key.endswith("/") or not key.lower().endswith(".pdf"):
                continue
            names.append(os.path.basename(key))
    return sorted(set(names))


def download_guideline(filename: str, dest_path: str) -> None:
    client = get_s3_client()
    key = f"{GUIDELINES_PREFIX}{filename}"
    client.download_file(BUCKET_NAME, key, dest_path)


class GuidelinesCache:
    """In-memory cache with TTL and stale fallback for S3 listing."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._names: List[str] = []
        self._fetched_at: float = 0.0

    def get(self, force_refresh: bool = False) -> List[str]:
        now = time.time()
        stale = self._names and (now - self._fetched_at) >= self.ttl_seconds
        if self._names and not force_refresh and not stale:
            return list(self._names)

        try:
            names = list_guideline_pdfs()
            if names:
                self._names = names
                self._fetched_at = now
                logger.info("Guidelines cache refreshed (%d PDFs)", len(names))
                return list(names)
        except Exception as exc:
            logger.exception("S3 guidelines list failed: %s", exc)
            if self._names:
                logger.warning("Returning stale guidelines cache (%d PDFs)", len(self._names))
                return list(self._names)
            raise

        return list(self._names)


guidelines_cache = GuidelinesCache()
