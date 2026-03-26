from openai import OpenAI
import os
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client =  OpenAI(api_key=api_key)

def select_guideline(case_text):

    guidelines = os.listdir("data/guidelines")

    prompt = f"""
You are a medical expert.

Given the case, select the MOST RELEVANT guideline file.

Return ONLY the file name.

Available guidelines:
{guidelines}

CASE:
{case_text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()