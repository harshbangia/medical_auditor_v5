import faiss
import numpy as np
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_embedding(text):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


def chunk_text(text, chunk_size=800, overlap=100):
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap

    return chunks


def build_vector_store(text):

    chunks = chunk_text(text)

    print(f"📦 Total chunks: {len(chunks)}")

    # 🔥 BATCH EMBEDDINGS
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=chunks
    )

    embeddings = [e.embedding for e in response.data]

    embeddings = np.array(embeddings).astype("float32")

    index = faiss.IndexFlatL2(len(embeddings[0]))
    index.add(embeddings)

    return index, chunks


def search(index, chunks, query, top_k=5):
    query_embedding = np.array([get_embedding(query)]).astype("float32")

    distances, indices = index.search(query_embedding, top_k)

    return "\n\n".join([chunks[i] for i in indices[0]])