import base64
import os
import re
from io import BytesIO

import fitz
import pytesseract
from PIL import Image

try:
    from pdf2image import convert_from_path  # poppler-backed renderer, optional
    _PDF2IMAGE_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    convert_from_path = None  # type: ignore
    _PDF2IMAGE_AVAILABLE = False

MIN_NATIVE_TEXT = 1000
LOW_PAGE_TEXT = 80
MAX_VISION_IMAGES = int(os.getenv("MAX_VISION_IMAGES", "12"))
OCR_WORKERS = max(1, min(int(os.getenv("OCR_WORKERS", "2")), 2))
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "30"))
PDF_DPI = int(os.getenv("PDF_DPI", "150"))

# Vision-based transcription of scanned / handwritten pages.
# Tesseract is unreliable on doctors' handwriting, prescriptions, receipts,
# ID cards, hospital letterheads, etc. — so we route those pages to an LLM
# and fold the transcribed text back into the case text.
VISION_OCR_ENABLED = os.getenv("VISION_OCR_ENABLED", "1") not in ("0", "false", "False", "")
# Default to gpt-4o (not mini) — handwritten medical notes need the bigger model.
VISION_OCR_MODEL = os.getenv("VISION_OCR_MODEL", "gpt-4o")
MAX_VISION_OCR_PAGES = int(os.getenv("MAX_VISION_OCR_PAGES", "15"))
VISION_OCR_DPI = int(os.getenv("VISION_OCR_DPI", "220"))
VISION_OCR_MIN_NATIVE_CHARS = int(os.getenv("VISION_OCR_MIN_NATIVE_CHARS", "120"))

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


def _render_pages_fitz(pdf_path, first_page=None, last_page=None, dpi=PDF_DPI):
    """Render PDF pages to PIL images using PyMuPDF (no poppler required)."""
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        start = (first_page or 1) - 1
        end = (last_page or total)
        start = max(0, start)
        end = min(total, end)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pages = []
        for idx in range(start, end):
            pix = doc[idx].get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pages.append(img)
        return pages
    finally:
        doc.close()


def _convert_pages(pdf_path, first_page=None, last_page=None):
    """Render pages via pdf2image/poppler when available; otherwise via PyMuPDF."""
    if _PDF2IMAGE_AVAILABLE:
        try:
            kwargs = {"dpi": PDF_DPI, "fmt": "jpeg"}
            if first_page is not None:
                kwargs["first_page"] = first_page
                kwargs["last_page"] = last_page or first_page
            return convert_from_path(pdf_path, **kwargs)
        except Exception as exc:
            print(f"⚠️ pdf2image render failed ({exc}); falling back to PyMuPDF")
    return _render_pages_fitz(pdf_path, first_page=first_page, last_page=last_page, dpi=PDF_DPI)


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


_VISION_OCR_PROMPT = """You are an expert medical-records transcriber working for an INSURANCE MEDICAL AUDITOR.
You read Indian hospital documents fluently, including doctors' cursive handwriting.

The image is one page of a hospital claim file. It may be:
- a handwritten doctor's consultation note, prescription, or operative note
- a handwritten or printed money receipt / bill / invoice
- a hospital letterhead, discharge summary, admission slip
- an insurance/ID card, policy page, pre-auth form
- a lab report, radiology report, or referral letter

Your job: TRANSCRIBE every piece of text on the page faithfully and in reading order, including
handwritten content. Do not summarise. Do not invent. Preserve dates, drug names, dosages,
amounts, patient/doctor names, registration numbers, hospital names, signatures, stamps.

DISAMBIGUATION RULES (very important for handwriting):
- Use ALL contextual evidence on the page — printed letterhead specialty (e.g. "Neurosurgeon",
  "Cardiologist", "Oncologist"), hospital name, planned procedure, imaging finding, and
  prescribed drugs — to choose between visually-similar words. The diagnosis must be
  consistent with the doctor's specialty and the rest of the note.
- Recognise standard medical abbreviations: MVD = microvascular decompression;
  CABG = coronary artery bypass; TURP = transurethral resection of prostate;
  s/o = suggestive of; c/o = complaints of; k/c/o = known case of; Pt. = patient;
  Ⓡ / (R) = right, Ⓛ / (L) = left.
- If a handwritten word has two plausible readings, pick the one that is medically
  consistent with the rest of the page, and list the alternative in UNCERTAIN with a
  brief reason. Example: "diagnosis read as 'Trigeminal Neuralgia' (not 'Inguinal') —
  consistent with Neurosurgeon letterhead, MRI showing neurovascular conflict, and
  planned MVD which is the canonical TN procedure."
- Never silently change a word — every interpretive choice goes in UNCERTAIN.
- If you genuinely cannot read a word, write [?] in BODY and list it in UNCERTAIN.

Output format (plain text, no JSON, no markdown fences):

DOCUMENT TYPE: <one short phrase, e.g. "Handwritten consultation note", "Money receipt", "Insurance ID card">
HEADER / LETTERHEAD: <printed letterhead text if any, including specialty / hospital / regn no>
BODY:
<verbatim transcription, line by line; keep handwritten structure>
FOOTER / STAMPS / SIGNATURES: <names, registration numbers, stamps>
UNCERTAIN: <ambiguous words with chosen reading, alternative reading, and the contextual reason; one per line>

If the page is genuinely blank or contains only a logo with no readable text, reply exactly:
BLANK PAGE
"""


def _transcribe_page_with_vision(image_b64: str, page_num: int, source_name: str = "") -> str:
    """Use an LLM to transcribe a scanned / handwritten PDF page into plain text."""
    if not VISION_OCR_ENABLED or not image_b64:
        return ""
    try:
        from backend.llm_client import get_openai_client
        from backend.ai.llm_helpers import extract_response_text, image_input_part
    except Exception as exc:
        print(f"⚠️ Vision transcription unavailable (import error): {exc}")
        return ""

    label = f"Page {page_num}"
    if source_name:
        label += f" of {source_name}"

    content = [
        {"type": "input_text", "text": _VISION_OCR_PROMPT},
        {"type": "input_text", "text": label},
        image_input_part(image_b64, detail="high"),
    ]

    try:
        client = get_openai_client()
        response = client.responses.create(
            model=VISION_OCR_MODEL,
            input=[{"role": "user", "content": content}],
        )
        text = (extract_response_text(response) or "").strip()
        if not text or text.upper().startswith("BLANK PAGE"):
            return ""
        return text
    except Exception as exc:
        print(f"⚠️ Vision transcription failed for page {page_num}: {exc}")
        return ""


def _page_image_for_transcription(doc, pdf_path: str, page_num: int) -> str:
    """Prefer the embedded page image (faster, no re-render); else render via fitz."""
    try:
        page = doc[page_num - 1]
        embedded = page.get_images(full=True)
        for img in embedded:
            try:
                base_image = doc.extract_image(img[0])
                raw = base_image.get("image") or b""
                width = int(base_image.get("width") or 0)
                height = int(base_image.get("height") or 0)
                if len(raw) < 8000 or width < 400 or height < 400:
                    continue
                return base64.b64encode(raw).decode()
            except Exception:
                continue
    except Exception:
        pass

    try:
        rendered = _render_pages_fitz(pdf_path, page_num, page_num, dpi=VISION_OCR_DPI)
        if rendered:
            return _page_to_b64(rendered[0], quality=80)
    except Exception as exc:
        print(f"⚠️ Could not render page {page_num} for vision transcription: {exc}")
    return ""


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

    page_ocr_text = {m["page"]: m["text"] for m in page_meta}

    if native_len < MIN_NATIVE_TEXT:
        if native_len > 200:
            low_pages = [m["page"] for m in page_meta if len(m["text"].strip()) < LOW_PAGE_TEXT]
            print(f"⚠️ Partial native text ({native_len} chars) → selective OCR on {len(low_pages)} pages")
            try:
                ocr_parts = []
                for page_num in low_pages[:MAX_OCR_PAGES]:
                    rendered = _convert_pages(pdf_path, page_num, page_num)
                    if rendered:
                        ocr_text = _ocr_image(rendered[0])
                        ocr_parts.append(ocr_text)
                        page_ocr_text[page_num] = (page_ocr_text.get(page_num, "") + "\n" + ocr_text).strip()
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
                for idx, ocr_text in enumerate(ocr_parts):
                    page_num = idx + 1
                    page_ocr_text[page_num] = (page_ocr_text.get(page_num, "") + "\n" + ocr_text).strip()
                full_text = full_text + "\n" + "\n".join(ocr_parts)
            except Exception as e:
                print("❌ OCR FAILED:", e)
    else:
        print(f"✅ Skipping OCR ({native_len} chars native text)")

    # Vision-based transcription of pages that STILL have very little usable text.
    # This catches scanned / handwritten pages that Tesseract cannot read
    # (doctor's notes, prescriptions, money receipts, ID cards, etc.) and folds
    # the transcribed text back into the case so the audit pipeline can use it.
    if VISION_OCR_ENABLED:
        candidates = []
        for meta in page_meta:
            page_num = meta["page"]
            text_after_ocr = (page_ocr_text.get(page_num) or "").strip()
            if len(text_after_ocr) >= VISION_OCR_MIN_NATIVE_CHARS:
                continue
            if meta["image_count"] == 0 and len(text_after_ocr) > 0:
                # Truly text-light page with no image — nothing for vision to read.
                continue
            candidates.append(page_num)

        if candidates:
            if len(candidates) > MAX_VISION_OCR_PAGES:
                print(
                    f"⚠️ Capping vision transcription to first {MAX_VISION_OCR_PAGES} "
                    f"of {len(candidates)} text-light pages"
                )
                candidates = candidates[:MAX_VISION_OCR_PAGES]
            print(
                f"🧠 Vision transcription on {len(candidates)} text-light page(s) "
                f"using {VISION_OCR_MODEL}"
            )
            doc_for_ocr = fitz.open(pdf_path)
            transcribed_blocks = []
            try:
                for page_num in candidates:
                    image_b64 = _page_image_for_transcription(doc_for_ocr, pdf_path, page_num)
                    if not image_b64:
                        continue
                    transcript = _transcribe_page_with_vision(image_b64, page_num, source_name)
                    if not transcript:
                        continue
                    header = (
                        f"=== Page {page_num} — vision transcription"
                        f"{f' ({source_name})' if source_name else ''} ==="
                    )
                    block = f"{header}\n{transcript}"
                    transcribed_blocks.append(block)
                    page_ocr_text[page_num] = (
                        page_ocr_text.get(page_num, "") + "\n" + transcript
                    ).strip()
            finally:
                doc_for_ocr.close()
            if transcribed_blocks:
                full_text = (full_text + "\n\n" + "\n\n".join(transcribed_blocks)).strip()
                added_chars = sum(len(b) for b in transcribed_blocks)
                print(f"✅ Vision transcription added {added_chars} chars from {len(transcribed_blocks)} page(s)")
            else:
                print("⚠️ Vision transcription returned nothing usable")

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
