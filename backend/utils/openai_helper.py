def extract_text(response):
    """
    Safe extractor for OpenAI responses.
    NEVER causes 'Stream consumed'
    """
    text = ""

    try:
        if hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for c in item.content:
                        if hasattr(c, "text"):
                            text += c.text
    except Exception as e:
        raise Exception(f"Response parsing failed: {str(e)}")

    return text.strip()