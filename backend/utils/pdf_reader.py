import base64
import os
import re
from io import BytesIO

import fitz
import pytesseract
from pdf2image import convert_from_path

MIN_NATIVE_TEXT = 1000
LOW_PAGE_TEXT = 80
MAX_VISION_IMAGES = int(os.getenv("MAX_VISION_IMAGES", "12"))
OCR_WORKERS = max(1, min(int(os.getenv("OCR_WORKERS", "2")), 2))
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "30"))
PDF_DPI = int(os.getenv("PDF_DPI", "150"))

_CLINICAL_KEYWORDS = re.compile(
    r"\b(x-?ray|radiograph|ct\s|mri|ultrasound|usg|ecg|ekg|echo|histopath|biopsy|"
    r"specimen|wound|lesion|ulcer|fracture|scan|impression|film|mammogram|"
    r"doppler|endoscopy|colonoscopy|laparoscop|operative photo|clinical photo|"
    r"pre-?op|post-?op|specimen|gram stain|cytology)\b",
    re.I,
)


def _page_to_b64(pil_image, quality=70):
    buffered = BytesIO()
    pil_image.save(buffered, format="JPEG", quality=quality)
    return base64.b64encode(buffered.getvalue()).decode()


def _ocr_image(img):
    return pytesseract.image_to_string(img)


def _parallel_ocr(pil_pages):
    if not pil_pages:
        return []
    if len(pil_pages) > MAX_OCR_PAGES:
        print(f"⚠️ Capping OCR to first {MAX_OCR_PAGES} of {len(pil_pages)} pages")
        pil_pages = pil_pages[:MAX_OCR_PAGES]
    workers = min(OCR_WORKERS, len(pil_pages))
    if workers <= 1:
        return [_ocr_image(p) for p in pil_pages]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_ocr_image, pil_pages))


def _convert_pages(pdf_path, first_page=None, last_page=None):
    kwargs = {"dpi": PDF_DPI, "fmt": "jpeg"}
    if first_page is not None:
        kwargs["first_page"] = first_page
        kwargs["last_page"] = last_page or first_page
    return convert_from_path(pdf_path, **kwargs)


def _vision_score(page_text: str, image_count: int, page_area: float) -> int:
    score = 0
    text_len = len((page_text or "").strip())
    if text_len < LOW_PAGE_TEXT:
        score += 3
    if image_count > 0:
        score += 2 + min(image_count, 3)
    if _CLINICAL_KEYWORDS.search(page_text or ""):
        score += 4
    if page_area > 400000:
        score += 1
    return score


def _extract_embedded_images(doc, page_num, limit=3):
    """Extract up to `limit` embedded images from one page."""
    images = []
    page = doc[page_num - 1]
    for img in page.get_images(full=True):
        if len(images) >= limit:
            break
        try:
            base_image = doc.extract_image(img[0])
            raw = base_image.get("image") or b""
            if len(raw) < 3000:
                continue
            images.append(
                {
                    "base64": base64.b64encode(raw).decode(),
                    "page": page_num,
                    "kind": "embedded",
                }
            )
        except Exception:
            continue
    return images


def _render_page_image(pdf_path, page_num):
    rendered = _convert_pages(pdf_path, page_num, page_num)
    if not rendered:
        return None
    return {"base64": _page_to_b64(rendered[0]), "page": page_num, "kind": "render"}


def extract_text_and_images(pdf_path, source_name: str = ""):
    text_parts = []
    page_meta = []
    page_count = 0

    doc = fitz.open(pdf_path)
    page_count = len(doc)

    for i, page in enumerate(doc):
        page_num = i + 1
        page_text = page.get_text() or ""
        text_parts.append(page_text)
        img_count = len(page.get_images(full=True))
        page_meta.append(
            {
                "page": page_num,
                "text": page_text,
                "image_count": img_count,
                "score": _vision_score(page_text, img_count, page.rect.width * page.rect.height),
            }
        )

    full_text = "\n".join(text_parts)
    native_len = len(full_text.strip())
    doc.close()

    if native_len < MIN_NATIVE_TEXT:
        if native_len > 200:
            low_pages = [m["page"] for m in page_meta if len(m["text"].strip()) < LOW_PAGE_TEXT]
            print(f"⚠️ Partial native text ({native_len} chars) → selective OCR on {len(low_pages)} pages")
            try:
                ocr_parts = []
                for page_num in low_pages[:MAX_OCR_PAGES]:
                    rendered = _convert_pages(pdf_path, page_num, page_num)
                    if rendered:
                        ocr_parts.append(_ocr_image(rendered[0]))
                        del rendered
                full_text = full_text + "\n" + "\n".join(ocr_parts)
            except Exception as e:
                print("❌ Selective OCR failed:", e)
        else:
            print(f"⚠️ Low native text ({native_len} chars) → full OCR")
            try:
                rendered_pages = _convert_pages(pdf_path)
                print(f"🧠 OCR {len(rendered_pages)} pages (workers={OCR_WORKERS})")
                ocr_parts = _parallel_ocr(rendered_pages)
                del rendered_pages
                full_text = full_text + "\n" + "\n".join(ocr_parts)
            except Exception as e:
                print("❌ OCR FAILED:", e)
    else:
        print(f"✅ Skipping OCR ({native_len} chars native text)")

    # Vision candidates: rank pages by clinical relevance
    ranked_pages = sorted(page_meta, key=lambda m: m["score"], reverse=True)
    images = []
    seen_pages = set()

    doc = fitz.open(pdf_path)
    for meta in ranked_pages:
        if len(images) >= MAX_VISION_IMAGES:
            break
        page_num = meta["page"]
        if page_num in seen_pages:
            continue

        embedded = _extract_embedded_images(doc, page_num, limit=2)
        for img in embedded:
            if len(images) >= MAX_VISION_IMAGES:
                break
            img["source"] = source_name
            images.append(img)
            seen_pages.add(page_num)

        # Render page if high-score but no usable embedded image, or scan-like page
        needs_render = meta["score"] >= 4 and not embedded
        if needs_render and len(images) < MAX_VISION_IMAGES:
            try:
                rendered = _render_page_image(pdf_path, page_num)
                if rendered:
                    rendered["source"] = source_name
                    images.append(rendered)
                    seen_pages.add(page_num)
            except Exception as e:
                print(f"⚠️ Page render failed p{page_num}:", e)

    doc.close()

    if len(images) > MAX_VISION_IMAGES:
        images = images[:MAX_VISION_IMAGES]

    print(
        f"📄 {source_name or pdf_path}: {len(full_text)} chars, "
        f"{len(images)} vision images ({page_count} pages)"
    )
    return full_text, images


def extract_text_from_pdf(pdf_path):
    """Text-only extraction for guideline indexing (no vision pass)."""
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
        pages = _convert_pages(pdf_path)
        print(f"🧠 OCR {len(pages)} pages (workers={OCR_WORKERS})")
        ocr_parts = _parallel_ocr(pages)
        del pages
        return text + "\n" + "\n".join(ocr_parts)
    except Exception as e:
        print("❌ OCR FAILED:", str(e))
    return text


def process_pdf_file(file_bytes: bytes, filename: str = "upload.pdf"):
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            tmp_path = tmp.name
        text, imgs = extract_text_and_images(tmp_path, source_name=filename)
        return {"filename": filename, "text": text, "images": imgs, "error": None}
    except Exception as exc:
        return {"filename": filename, "text": "", "images": [], "error": str(exc)}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
