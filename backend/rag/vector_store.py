import os
import re

import faiss
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_CLINICAL_BOOST = [
    "diagnosis", "treatment", "surgery", "biopsy", "indication", "contraindication",
    "admission", "discharge", "investigation", "imaging", "x-ray", "ct", "mri",
    "medical necessity", "documentation", "protocol", "criteria", "complication",
    "consent", "pre-authorization", "exclusion", "deviation", "monitoring",
]


def get_embedding(text):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding


def chunk_text(text, chunk_size=1200, overlap=150):
    """Split on paragraph boundaries where possible for cleaner retrieval."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= chunk_size:
                current = para
            else:
                start = 0
                while start < len(para):
                    chunks.append(para[start : start + chunk_size])
                    start += chunk_size - overlap
                current = ""
    if current:
        chunks.append(current)

    if not chunks and text.strip():
        start = 0
        while start < len(text):
            chunks.append(text[start : start + chunk_size])
            start += chunk_size - overlap
    return chunks


def _score_chunk(chunk: str) -> int:
    lower = chunk.lower()
    return sum(1 for k in _CLINICAL_BOOST if k in lower)


def build_vector_store(text):
    chunks = chunk_text(text)
    print(f"📦 Total chunks: {len(chunks)}")

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=chunks,
    )
    embeddings = [e.embedding for e in response.data]
    embeddings = np.array(embeddings).astype("float32")

    index = faiss.IndexFlatL2(len(embeddings[0]))
    index.add(embeddings)
    return index, chunks


def search(index, chunks, query, top_k=5):
    return search_multi(index, chunks, [query], top_k_each=top_k, max_total=top_k)


def search_multi(index, chunks, queries: list, top_k_each: int = 4, max_total: int = 12) -> str:
    """Multi-query retrieval with deduplication and clinical re-ranking."""
    if not queries:
        return ""

    ranked = []
    seen_text = set()

    for query in queries:
        if not query or not query.strip():
            continue
        query_embedding = np.array([get_embedding(query)]).astype("float32")
        fetch_k = min(top_k_each * 3, len(chunks))
        if fetch_k < 1:
            continue
        _, indices = index.search(query_embedding, fetch_k)

        for i in indices[0]:
            if i < 0 or i >= len(chunks):
                continue
            chunk = chunks[i]
            key = chunk[:200]
            if key in seen_text:
                continue
            seen_text.add(key)
            ranked.append((_score_chunk(chunk), chunk))

    ranked.sort(key=lambda x: x[0], reverse=True)
    selected = [c for _, c in ranked[:max_total]]
    return "\n\n---\n\n".join(selected)
