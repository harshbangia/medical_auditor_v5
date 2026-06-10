import base64
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import fitz
import pytesseract
from pdf2image import convert_from_path

MIN_NATIVE_TEXT = 1000
LOW_PAGE_TEXT = 80
MAX_VISION_IMAGES = 8
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "4"))


def _page_to_b64(pil_image, quality=75):
    buffered = BytesIO()
    pil_image.save(buffered, format="JPEG", quality=quality)
    return base64.b64encode(buffered.getvalue()).decode()


def _ocr_image(img):
    return pytesseract.image_to_string(img)


def _parallel_ocr(pil_pages):
    if not pil_pages:
        return []
    workers = min(OCR_WORKERS, len(pil_pages))
    if workers <= 1:
        return [_ocr_image(p) for p in pil_pages]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_ocr_image, pil_pages))


def extract_text_from_pdf(pdf_path):
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
        pages = convert_from_path(pdf_path)
        print(f"🧠 OCR {len(pages)} pages (parallel x{min(OCR_WORKERS, len(pages))})")
        ocr_parts = _parallel_ocr(pages)
        return text + "\n" + "\n".join(ocr_parts)
    except Exception as e:
        print("❌ OCR FAILED:", str(e))
    return text


def _extract_embedded_images(doc, limit=MAX_VISION_IMAGES):
    images = []
    for page_num, page in enumerate(doc, start=1):
        for img in page.get_images(full=True):
            if len(images) >= limit:
                return images
            try:
                xref = img[0]
                base_image = doc.extract_image(xref)
                if len(base_image.get("image") or b"") < 5000:
                    continue
                images.append(
                    {
                        "base64": base64.b64encode(base_image["image"]).decode(),
                        "page": page_num,
                    }
                )
            except Exception:
                continue
    return images


def extract_text_and_images(pdf_path):
    """
    Fast path for text-native PDFs; parallel OCR for scans.
    Vision images: embedded photos only when text is sufficient.
    """
    text_parts = []
    low_text_page_nums = []
    clinical_render_pages = []
    page_count = 0

    doc = fitz.open(pdf_path)
    page_count = len(doc)

    for i, page in enumerate(doc):
        page_text = page.get_text() or ""
        text_parts.append(page_text)
        if len(page_text.strip()) < LOW_PAGE_TEXT:
            low_text_page_nums.append(i + 1)
            imgs_on_page = page.get_images(full=True)
            if imgs_on_page and len(imgs_on_page) >= 2:
                clinical_render_pages.append(i + 1)

    full_text = "\n".join(text_parts)
    native_len = len(full_text.strip())

    if native_len < MIN_NATIVE_TEXT:
        if native_len > 200 and low_text_page_nums:
            print(f"⚠️ Partial native text ({native_len} chars) → OCR on {len(low_text_page_nums)} pages")
            try:
                ocr_parts = []
                for page_num in low_text_page_nums:
                    rendered = convert_from_path(
                        pdf_path, first_page=page_num, last_page=page_num
                    )
                    if rendered:
                        ocr_parts.append(_ocr_image(rendered[0]))
                full_text = full_text + "\n" + "\n".join(ocr_parts)
            except Exception as e:
                print("❌ Selective OCR failed:", e)
        else:
            print(f"⚠️ Low native text ({native_len} chars) → full parallel OCR")
            try:
                rendered_pages = convert_from_path(pdf_path)
                print(f"🧠 OCR {len(rendered_pages)} pages (parallel x{min(OCR_WORKERS, len(rendered_pages))})")
                ocr_parts = _parallel_ocr(rendered_pages)
                full_text = full_text + "\n" + "\n".join(ocr_parts)
                clinical_render_pages = low_text_page_nums[:MAX_VISION_IMAGES]
            except Exception as e:
                print("❌ OCR FAILED:", e)
    else:
        print(f"✅ Skipping OCR ({native_len} chars native text)")

    if native_len >= MIN_NATIVE_TEXT:
        images = _extract_embedded_images(doc, limit=MAX_VISION_IMAGES)
    else:
        images = _extract_embedded_images(doc, limit=MAX_VISION_IMAGES)
        seen = {img["page"] for img in images}
        for page_num in clinical_render_pages[:MAX_VISION_IMAGES]:
            if page_num in seen:
                continue
            try:
                rendered = convert_from_path(
                    pdf_path, first_page=page_num, last_page=page_num
                )
                if rendered:
                    images.append({"base64": _page_to_b64(rendered[0]), "page": page_num})
                    seen.add(page_num)
            except Exception as e:
                print(f"⚠️ Page render failed p{page_num}:", e)

    doc.close()

    if len(images) > MAX_VISION_IMAGES:
        step = len(images) / MAX_VISION_IMAGES
        images = [images[int(i * step)] for i in range(MAX_VISION_IMAGES)]

    print(f"📄 Done: {len(full_text)} chars, {len(images)} vision images ({page_count} pages)")
    return full_text, images


def process_pdf_file(file_bytes: bytes, filename: str = "upload.pdf"):
    """Process one uploaded PDF (for parallel workers)."""
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            tmp_path = tmp.name
        text, imgs = extract_text_and_images(tmp_path)
        return {"filename": filename, "text": text, "images": imgs, "error": None}
    except Exception as exc:
        return {"filename": filename, "text": "", "images": [], "error": str(exc)}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
