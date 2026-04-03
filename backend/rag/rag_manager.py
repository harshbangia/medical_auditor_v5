import os
from backend.utils.pdf_reader import extract_text_from_pdf
from backend.rag.vector_store import build_vector_store

RAG_CACHE = {}

def get_or_create_index(guideline_file):

    if guideline_file in RAG_CACHE:
        return RAG_CACHE[guideline_file]

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    guideline_path = os.path.join(BASE_DIR, "data", "guidelines", guideline_file)

    if not os.path.exists(guideline_path):
        raise Exception("Guideline not found")

    print(f"📘 Building RAG for: {guideline_file}")

    text = extract_text_from_pdf(guideline_path)
    text = text[:300000]

    index, chunks = build_vector_store(text)

    RAG_CACHE[guideline_file] = (index, chunks)

    return index, chunks