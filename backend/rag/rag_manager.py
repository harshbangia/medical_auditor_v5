import hashlib
import os
import pickle
import tempfile

import faiss

from backend.utils.pdf_reader import extract_text_from_pdf
from backend.rag.vector_store import build_vector_store
from backend.services.s3_utils import download_guideline

RAG_CACHE = {}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DISK_CACHE_DIR = os.path.join(BASE_DIR, ".rag_cache")


def _disk_paths(cache_key: str):
    safe = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:32]
    folder = os.path.join(DISK_CACHE_DIR, safe)
    return (
        folder,
        os.path.join(folder, "index.faiss"),
        os.path.join(folder, "chunks.pkl"),
    )


def _load_disk(cache_key: str):
    folder, index_path, chunks_path = _disk_paths(cache_key)
    if not os.path.isfile(index_path) or not os.path.isfile(chunks_path):
        return None
    try:
        index = faiss.read_index(index_path)
        with open(chunks_path, "rb") as fh:
            chunks = pickle.load(fh)
        print(f"💾 Loaded RAG from disk: {cache_key}")
        return index, chunks
    except Exception as exc:
        print(f"⚠️ Disk RAG load failed ({cache_key}): {exc}")
        return None


def _save_disk(cache_key: str, index, chunks) -> None:
    folder, index_path, chunks_path = _disk_paths(cache_key)
    try:
        os.makedirs(folder, exist_ok=True)
        faiss.write_index(index, index_path)
        with open(chunks_path, "wb") as fh:
            pickle.dump(chunks, fh)
        print(f"💾 Saved RAG to disk: {cache_key}")
    except Exception as exc:
        print(f"⚠️ Disk RAG save failed ({cache_key}): {exc}")


def get_or_create_index(guideline_file, cache_key=None):
    """
    Build or reuse FAISS index for a guideline PDF.
    cache_key MUST be the stable guideline filename (not a temp path).
    Index is versioned by embedding model so OpenAI→Gemini swaps do not mix vectors.
    """
    from backend.llm.models import embedding_model, get_provider_name

    if cache_key:
        key = cache_key.strip()
    elif os.path.isabs(guideline_file):
        key = os.path.basename(guideline_file)
    else:
        key = guideline_file.strip()

    # Embed provider + model into cache identity (dimension / space differ)
    versioned_key = f"{key}::{get_provider_name()}::{embedding_model()}"

    if versioned_key in RAG_CACHE:
        print(f"⚡ Using in-memory RAG: {versioned_key}")
        return RAG_CACHE[versioned_key]

    disk = _load_disk(versioned_key)
    if disk:
        RAG_CACHE[versioned_key] = disk
        return disk

    if os.path.isabs(guideline_file) and os.path.exists(guideline_file):
        guideline_path = guideline_file
    else:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            download_guideline(key, tmp.name)
            guideline_path = tmp.name

    print(f"📂 USING PATH: {guideline_path}")
    if not os.path.exists(guideline_path):
        raise FileNotFoundError(f"Guideline not found: {key}")

    print(f"📘 Building RAG for: {versioned_key}")
    text = extract_text_from_pdf(guideline_path)[:300000]
    index, chunks = build_vector_store(text)
    RAG_CACHE[versioned_key] = (index, chunks)
    _save_disk(versioned_key, index, chunks)

    if guideline_path != guideline_file and os.path.exists(guideline_path):
        try:
            os.remove(guideline_path)
        except OSError:
            pass

    return index, chunks
