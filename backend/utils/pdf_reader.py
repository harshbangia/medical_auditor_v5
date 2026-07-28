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
MAX_VISION_OCR_PAGES = int(os.getenv("MAX_VISION_OCR_PAGES", "25"))
VISION_OCR_DPI = int(os.getenv("VISION_OCR_DPI", "220"))
VISION_OCR_MIN_NATIVE_CHARS = int(os.getenv("VISION_OCR_MIN_NATIVE_CHARS", "120"))

_CLINICAL_KEYWORDS = re.compile(
    r"\b(x-?ray|radiograph|ct\s|mri|ultrasound|usg|ecg|ekg|echo|histopath|biopsy|"
    r"specimen|wound|lesion|ulcer|fracture|scan|impression|film|mammogram|"
    r"doppler|endoscopy|colonoscopy|laparoscop|operative photo|clinical photo|"
    r"pre-?op|post-?op|specimen|gram stain|cytology)\b",
    re.I,
)

# Hospital HIS often stamps a typed demographic banner on every scanned page.
# Those ~100–150 chars look like "enough text" and used to SKIP vision OCR —
# so handwriting on the page was never read.
_HEADER_OVERLAY_RE = re.compile(
    r"Patient\s*Name\s*:.*?(?:UHID|IPD|Gender|Age)",
    re.I | re.S,
)


def _is_header_only_overlay(text: str) -> bool:
    """True when page text is only a HIS demographic banner (body is a scan)."""
    t = (text or "").strip()
    if not t:
        return True
    if len(t) > 280:
        return False
    if not _HEADER_OVERLAY_RE.search(t):
        return False
    remainder = _HEADER_OVERLAY_RE.sub("", t)
    remainder = re.sub(
        r"\d{1,3}\s*Y(?:ears?)?\s*\d{0,2}\s*M(?:onths?)?\s*\d{0,2}\s*D(?:ays?)?",
        "",
        remainder,
        flags=re.I,
    )
    remainder = re.sub(r"\b(?:Male|Female|Gender|Age|UHID|IPD)\b[:\s]*", "", remainder, flags=re.I)
    remainder = re.sub(r"[^\w]+", " ", remainder).strip()
    return len(remainder) < 30


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


_TYPED_REPORT_TEXT_THRESHOLD = 600  # chars of native text that mark a page as a typed report


def _vision_score(page_text: str, image_count: int, page_area: float) -> int:
    """Rank pages for the IMAGE ANALYSIS vision pass.

    Goal: surface genuine clinical images (scans, photos, films) — NOT typed
    radiology reports or letters that happen to mention 'MRI' / 'CT' in their text.
    """
    score = 0
    text_len = len((page_text or "").strip())
    has_clinical_kw = bool(_CLINICAL_KEYWORDS.search(page_text or ""))

    # Page is text-heavy → it's almost certainly a typed report or letter,
    # not a clinical image to be visually interpreted. Vision OCR has already
    # transcribed it; the image-analysis pass should leave it alone.
    if text_len >= _TYPED_REPORT_TEXT_THRESHOLD:
        return 0

    if text_len < LOW_PAGE_TEXT:
        score += 3
    if image_count > 0:
        score += 2 + min(image_count, 3)
    # Clinical-keyword bonus only on text-light pages (real image pages),
    # never on typed pages that just mention 'MRI' in passing.
    if has_clinical_kw and text_len < _TYPED_REPORT_TEXT_THRESHOLD:
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
  EVD = external ventricular drain; s/o = suggestive of; c/o = complaints of;
  k/c/o = known case of; H/O = history of; Pt. = patient;
  Ⓡ / (R) = right, Ⓛ / (L) = left.
- PAST HISTORY vs CURRENT PROCEDURE (critical):
  - Text marked "H/O TURP", "Past History: TURP", "k/c/o …" is PRIOR history —
    transcribe under PAST HISTORY, NOT as the operation done this admission.
  - Current surgery appears under Operation / Procedure / Findings / Proposed Treatment.
- DEMOGRAPHICS (critical — typed banners beat handwriting):
  - Indian HIS overlays look like: "Patient Name : Mr GAGANDEEP SINGH GULATI UHID : LMH…
    Age : 49 Y 0 M 0 D". Copy these EXACTLY. Age is 49 — never invent 149 or merge digits.
  - Never split one given name into two words with weird capitals ("GaGa DEEP"). Prefer the
    printed UHID banner name when handwriting is unclear.
  - Hospital name is the FULL facility on the letterhead (e.g. "L. N. Medical College &
    J. K. Hospital"). Never output bare phrases like "Certified Hospital", "ISO 9001",
    or "NABH Accredited" as the hospital name.
  - UHID / IPD / Patient ID (often LMH…) is NOT the insurance policy number.
    Policy numbers are usually labeled Policy No / Insured ID (e.g. H7583101).
- MONEY: Prefer labeled totals ("Sum Total Expected Cost", "Grand Total", "Net Payable").
  Never treat a bare "20" (e.g. Mannitol 20%) as the hospital bill.
- If a handwritten word has two plausible readings, pick the one that is medically
  consistent with the rest of the page, and list the alternative in UNCERTAIN with a
  brief reason.
- Never silently change a word — every interpretive choice goes in UNCERTAIN.
- If you genuinely cannot read a word, write [?] in BODY and list it in UNCERTAIN.

PRESCRIPTION NOTATION (critical — do not confuse with symptom duration):
- On a PRESCRIPTION page, "x 2 mths" / "x 2 mo" / "× 2 months" means medicines were
  prescribed FOR 2 MONTHS — this is the TREATMENT COURSE, not how long the patient
  had symptoms.
- "F/u after 2 mths" / "Review after 2 mths" = follow-up after 2 MONTHS.
- In Indian handwriting, "mths" / "mo" / "m" on prescription pages means MONTHS;
  "wks" / "wk" means WEEKS. Do NOT read "mths" as "weeks".
- Symptom duration ("facial pain x 1 month") appears on the CONSULTATION note page;
  treatment course ("x 2 mths") appears on the PRESCRIPTION page — transcribe BOTH
  on their respective pages exactly as written.
- Common brands: Zenoxa/Zenoxo = oxcarbazepine; Dolokind/Dolonex = analgesic combo.

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
            header_only = _is_header_only_overlay(text_after_ocr)
            # Enough real body text → skip vision. Header-only HIS banners still need vision.
            if len(text_after_ocr) >= VISION_OCR_MIN_NATIVE_CHARS and not header_only:
                continue
            if meta["image_count"] == 0 and len(text_after_ocr) > 0 and not header_only:
                # Truly text-light page with no image — nothing for vision to read.
                # Header-only overlays usually sit on a full-page scan even when
                # PyMuPDF reports image_count=0 (content-stream drawings) — still try.
                continue
            # Prefer early clinical pages when capping
            priority = 0
            if header_only:
                priority += 2
            if page_num <= 8:
                priority += 3
            elif page_num <= 20:
                priority += 1
            if meta["image_count"] > 0:
                priority += 1
            candidates.append((priority, -page_num, page_num))

        candidates = [p for _, __, p in sorted(candidates, reverse=True)]
        if candidates:
            if len(candidates) > MAX_VISION_OCR_PAGES:
                print(
                    f"⚠️ Capping vision transcription to first {MAX_VISION_OCR_PAGES} "
                    f"of {len(candidates)} text-light / header-overlay pages"
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
                        # Header-only with image_count=0: force high-DPI render
                        try:
                            rendered = _render_pages_fitz(
                                pdf_path, page_num, page_num, dpi=max(VISION_OCR_DPI, 220)
                            )
                            if rendered:
                                image_b64 = _page_to_b64(rendered[0], quality=85)
                        except Exception:
                            image_b64 = ""
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


def _summarize_source(filename: str, text: str) -> dict:
    """Produce a per-PDF provenance summary for the audit report."""
    total = len(text or "")
    vision_chars = 0
    typed_chars = total
    if "vision transcription" in (text or ""):
        for block in (text or "").split("=== Page "):
            if "— vision transcription" in block:
                vision_chars += len(block)
        typed_chars = max(0, total - vision_chars)
    has_handwriting = vision_chars > 0
    return {
        "filename": filename,
        "total_chars": total,
        "typed_chars": typed_chars,
        "handwritten_or_scanned_chars": vision_chars,
        "contains_handwriting_or_scan": has_handwriting,
    }


def process_pdf_file(file_bytes: bytes, filename: str = "upload.pdf"):
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            tmp_path = tmp.name
        text, imgs = extract_text_and_images(tmp_path, source_name=filename)
        return {
            "filename": filename,
            "text": text,
            "images": imgs,
            "source_summary": _summarize_source(filename, text),
            "error": None,
        }
    except Exception as exc:
        return {
            "filename": filename,
            "text": "",
            "images": [],
            "source_summary": {
                "filename": filename,
                "total_chars": 0,
                "typed_chars": 0,
                "handwritten_or_scanned_chars": 0,
                "contains_handwriting_or_scan": False,
                "error": str(exc),
            },
            "error": str(exc),
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
