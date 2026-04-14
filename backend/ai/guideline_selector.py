from openai import OpenAI
import os
import boto3
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _list_guidelines():
    try:
        s3 = boto3.client("s3")
        bucket_name = "glowix-medical-auditor"
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix="guidelines/")
        files = []
        for obj in response.get("Contents", []):
            key = obj.get("Key", "")
            if not key or key.endswith("/") or not key.lower().endswith(".pdf"):
                continue
            files.append(os.path.basename(key))
        if files:
            return sorted(set(files))
    except Exception:
        pass
    if os.path.isdir("data/guidelines"):
        return [f for f in os.listdir("data/guidelines") if f.lower().endswith(".pdf")]
    return []

def extract_text(response):
    text = ""
    if hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        text += c.text
    return text.strip()


def select_guideline(case_text):

    guidelines = _list_guidelines()

    prompt = f"""
You are a medical expert.

Given the case, select the MOST RELEVANT guideline file.

Return ONLY the file name.

Available guidelines:
{guidelines}

CASE:
{case_text[:3000]}
"""

    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt
    )

    return extract_text(response)