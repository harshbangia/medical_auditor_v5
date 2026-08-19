"""Data models for the Case Notebook corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Citation:
    doc_id: str
    page: Optional[int] = None
    filename: str = ""
    excerpt: str = ""

    def label(self) -> str:
        page = f" p.{self.page}" if self.page else ""
        name = self.filename or self.doc_id
        return f"{name}{page}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NotebookChunk:
    chunk_id: str
    doc_id: str
    filename: str
    page: Optional[int]
    doc_type: str
    text: str
    source_kind: str = "native"  # native|ocr|vision|assessor

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaseNotebook:
    """In-memory grounded corpus for one audit job."""

    chunks: List[NotebookChunk] = field(default_factory=list)
    documents: List[Dict[str, Any]] = field(default_factory=list)
    assessor: Dict[str, Any] = field(default_factory=dict)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    fwa_findings: List[Dict[str, Any]] = field(default_factory=list)
    validated_ids: Dict[str, str] = field(default_factory=dict)
    finance_hints: Dict[str, Any] = field(default_factory=dict)
    # Full case text used for identity seal (chunks alone may miss headers)
    full_corpus: str = ""

    def corpus_text(self, max_chars: int = 180_000) -> str:
        if self.full_corpus:
            return self.full_corpus[:max_chars]
        parts: List[str] = []
        for ch in self.chunks:
            header = f"=== {ch.filename} | page={ch.page or '?'} | type={ch.doc_type} ==="
            parts.append(f"{header}\n{ch.text}")
        blob = "\n\n".join(parts)
        return blob[:max_chars]

    def search(self, query: str, top_k: int = 8) -> List[NotebookChunk]:
        q = (query or "").lower().strip()
        if not q:
            return []
        terms = [t for t in q.split() if len(t) > 2]
        scored: List[tuple] = []
        for ch in self.chunks:
            low = ch.text.lower()
            score = sum(1 for t in terms if t in low)
            if score:
                scored.append((score, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_count": len(self.documents),
            "chunk_count": len(self.chunks),
            "documents": self.documents,
            "assessor": self.assessor,
            "contradictions": self.contradictions,
            "fwa_findings": self.fwa_findings,
            "validated_ids": self.validated_ids,
            "finance_hints": self.finance_hints,
            # Keep chunk payloads for Ask / debugging; UI can ignore.
            "chunks": [c.to_dict() for c in self.chunks[:400]],
        }
