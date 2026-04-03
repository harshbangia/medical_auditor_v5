from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

    guidelines = os.listdir("data/guidelines")

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