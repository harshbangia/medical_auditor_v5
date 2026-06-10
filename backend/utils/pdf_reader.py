import base64
from io import BytesIO

import fitz  # PyMuPDF
from pdf2image import convert_from_path
import pytesseract

# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

MIN_NATIVE_TEXT = 1000
LOW_PAGE_TEXT = 80
MAX_VISION_IMAGES = 20


def _page_to_b64(pil_image, quality=85):
    buffered = BytesIO()
    pil_image.save(buffered, format="JPEG", quality=quality)
    return base64.b64encode(buffered.getvalue()).decode()


def extract_text_from_pdf(pdf_path):
    """Extract guideline/case text; OCR only when native text is sparse."""
    text = ""

    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text() or ""
        doc.close()
    except Exception as e:
        print("⚠️ PyMuPDF failed:", str(e))

    if len(text.strip()) > MIN_NATIVE_TEXT:
        print(f"✅ Using native PDF text ({len(text.strip())} chars)")
        return text

    print("⚠️ Low native text detected → running OCR...")
    try:
        images = convert_from_path(pdf_path)
        ocr_text = ""
        for i, img in enumerate(images):
            print(f"🧠 OCR page {i + 1}/{len(images)}")
            ocr_text += pytesseract.image_to_string(img)
        return text + "\n" + ocr_text
    except Exception as e:
        print("❌ OCR FAILED:", str(e))

    return text


def extract_images_from_pdf(pdf_path, limit=3):
    images_base64 = []

    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                images_base64.append(base64.b64encode(image_bytes).decode())
        doc.close()
    except Exception as e:
        print("Image extraction failed:", e)

    print(f"🖼️ Extracted {len(images_base64)} embedded images")
    return images_base64[:limit]


def extract_text_and_images(pdf_path):
    """
    Fast path for text-native PDFs; OCR + page renders only when needed.
    Returns (text, images) where each image is {"base64": str, "page": int}.
    """
    text_parts = []
    pages_needing_render = set()
    page_count = 0

    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)

        for i, page in enumerate(doc):
            page_text = page.get_text() or ""
            text_parts.append(page_text)

            has_embedded = bool(page.get_images(full=True))
            if len(page_text.strip()) < LOW_PAGE_TEXT or has_embedded:
                pages_needing_render.add(i)

        doc.close()
    except Exception as e:
        print("⚠️ PyMuPDF failed:", str(e))

    full_text = "\n".join(text_parts)
    native_len = len(full_text.strip())

    if native_len < MIN_NATIVE_TEXT:
        print(f"⚠️ Low native text ({native_len} chars) — running OCR...")
        try:
            rendered_pages = convert_from_path(pdf_path)
            ocr_parts = []
            for i, img in enumerate(rendered_pages):
                print(f"🧠 OCR page {i + 1}/{len(rendered_pages)}")
                ocr_parts.append(pytesseract.image_to_string(img))
                pages_needing_render.add(i)
            full_text = full_text + "\n" + "\n".join(ocr_parts)
        except Exception as e:
            print("❌ OCR FAILED:", str(e))
    else:
        print(f"✅ Skipping full-document OCR ({native_len} chars of native text)")

    images = []
    embedded = extract_images_from_pdf(pdf_path, limit=MAX_VISION_IMAGES)
    for idx, b64 in enumerate(embedded):
        images.append({"base64": b64, "page": idx + 1})

    if pages_needing_render:
        try:
            for page_idx in sorted(pages_needing_render):
                page_num = page_idx + 1
                page_imgs = convert_from_path(
                    pdf_path, first_page=page_num, last_page=page_num
                )
                if not page_imgs:
                    continue
                images.append({"base64": _page_to_b64(page_imgs[0]), "page": page_num})
        except Exception as e:
            print("❌ Page render for vision failed:", str(e))

    # Deduplicate by page, cap total
    seen_pages = set()
    unique_images = []
    for img in images:
        key = img["page"]
        if key in seen_pages:
            continue
        seen_pages.add(key)
        unique_images.append(img)

    if len(unique_images) > MAX_VISION_IMAGES:
        step = len(unique_images) / MAX_VISION_IMAGES
        unique_images = [
            unique_images[int(i * step)] for i in range(MAX_VISION_IMAGES)
        ]

    print(
        f"📄 Done: {len(full_text)} chars, "
        f"{len(unique_images)} images for vision ({page_count} pages)"
    )
    return full_text, unique_images
