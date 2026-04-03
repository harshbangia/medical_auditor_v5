import os
from backend.utils.pdf_reader import extract_text_from_pdf
from backend.rag.vector_store import build_vector_store

RAG_CACHE = {}

def get_or_create_index(guideline_file):

    # ✅ HANDLE BOTH CASES (filename OR full path)
    if os.path.isabs(guideline_file):
        guideline_path = guideline_file
        cache_key = os.path.basename(guideline_file)
    else:
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        guideline_path = os.path.join(BASE_DIR, "data", "guidelines", guideline_file)
        cache_key = guideline_file

    print("📂 USING PATH:", guideline_path)

    if not os.path.exists(guideline_path):
        raise Exception(f"Guideline not found at path: {guideline_path}")

    # ✅ CACHE FIX
    if cache_key in RAG_CACHE:
        print("⚡ Using cached RAG")
        return RAG_CACHE[cache_key]

    print(f"📘 Building RAG for: {guideline_path}")

    text = extract_text_from_pdf(guideline_path)
    text = text[:300000]

    index, chunks = build_vector_store(text)

    RAG_CACHE[cache_key] = (index, chunks)

    return index, chunks