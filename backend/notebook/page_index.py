"""Parse case_text / doc_blocks into page-aware notebook chunks."""

from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Tuple

from backend.notebook.models import NotebookChunk
from backend.utils.claim_details_extractor import _classify_document

_SOURCE_RE = re.compile(r"=== Source document:\s*(.+?)\s*===", re.I)
_PAGE_VISION_RE = re.compile(
    r"=== Page\s+(\d+)\s*[—\-].*?vision transcription[^=]*===",
    re.I,
)
_PAGE_OCR_RE = re.compile(r"=== Page\s+(\d+)\s*[—\-].*?OCR[^=]*===", re.I)
_PAGE_PLAIN_RE = re.compile(r"=== Page\s+(\d+)\s*===", re.I)


def _doc_id(filename: str) -> str:
    base = (filename or "doc").strip() or "doc"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:40]
    return f"{safe}-{digest}"


def _split_pages(body: str) -> List[Tuple[Optional[int], str, str]]:
    """Return list of (page_no, text, source_kind)."""
    if not body or not body.strip():
        return []

    markers = []
    for rx, kind in (
        (_PAGE_VISION_RE, "vision"),
        (_PAGE_OCR_RE, "ocr"),
        (_PAGE_PLAIN_RE, "native"),
    ):
        for m in rx.finditer(body):
            markers.append((m.start(), m.end(), int(m.group(1)), kind))
    if not markers:
        return [(None, body.strip(), "native")]

    markers.sort(key=lambda x: x[0])
    pages: List[Tuple[Optional[int], str, str]] = []
    # Preamble before first page marker
    if markers[0][0] > 0:
        pre = body[: markers[0][0]].strip()
        if pre:
            pages.append((None, pre, "native"))
    for i, (start, end, page, kind) in enumerate(markers):
        stop = markers[i + 1][0] if i + 1 < len(markers) else len(body)
        text = body[end:stop].strip()
        if text:
            pages.append((page, text, kind))
    return pages or [(None, body.strip(), "native")]


def chunks_from_doc_blocks(
    doc_blocks: List[Tuple[str, str]],
) -> List[NotebookChunk]:
    chunks: List[NotebookChunk] = []
    for filename, text in doc_blocks or []:
        fname = filename or "upload.pdf"
        did = _doc_id(fname)
        doc_type = _classify_document(fname, text or "")
        for page, page_text, kind in _split_pages(text or ""):
            cid = f"{did}-p{page or 0}-{hashlib.md5(page_text[:200].encode()).hexdigest()[:6]}"
            chunks.append(
                NotebookChunk(
                    chunk_id=cid,
                    doc_id=did,
                    filename=fname,
                    page=page,
                    doc_type=doc_type,
                    text=page_text[:20000],
                    source_kind=kind,
                )
            )
    return chunks


def chunks_from_case_text(case_text: str) -> List[NotebookChunk]:
    """Fallback when only combined case_text is available."""
    if not case_text:
        return []
    parts = _SOURCE_RE.split(case_text)
    blocks: List[Tuple[str, str]] = []
    if len(parts) == 1:
        blocks = [("combined.pdf", case_text)]
    else:
        # split → [preamble, name1, body1, name2, body2, ...]
        i = 1
        while i + 1 < len(parts):
            blocks.append((parts[i].strip(), parts[i + 1]))
            i += 2
        if parts[0].strip():
            blocks.insert(0, ("preamble.pdf", parts[0]))
    return chunks_from_doc_blocks(blocks)
