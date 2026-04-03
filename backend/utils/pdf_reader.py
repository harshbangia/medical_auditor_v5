import fitz  # PyMuPDF
from pdf2image import convert_from_path
import pytesseract
import base64

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# =========================
# TEXT EXTRACTION
# =========================
def extract_text_from_pdf(pdf_path):
    import fitz

    text = ""

    try:
        doc = fitz.open(pdf_path)

        for page in doc:
            page_text = page.get_text()
            text += page_text

        doc.close()

        # ✅ If good text found → return
        if len(text.strip()) > 100:
            return text

    except Exception as e:
        print("⚠️ PyMuPDF failed:", str(e))

    # 🔥 OCR FALLBACK (CONTROLLED)
    try:
        from pdf2image import convert_from_path
        import pytesseract

        print("⚠️ Using OCR fallback...")

        # ✅ ONLY FIRST 10 PAGES (NOT FULL PDF)
        images = convert_from_path(pdf_path, first_page=1, last_page=10)

        for img in images:
            text += pytesseract.image_to_string(img)

    except Exception as e:
        print("❌ OCR FAILED:", str(e))

    return text


# =========================
# IMAGE EXTRACTION (NO AI)
# =========================
def extract_images_from_pdf(pdf_path):
    images_base64 = []

    try:
        doc = fitz.open(pdf_path)

        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]

                image_base64 = base64.b64encode(image_bytes).decode()
                images_base64.append(image_base64)

        doc.close()

    except Exception as e:
        print("Image extraction failed:", e)

    print(f"🖼️ Extracted {len(images_base64)} images")
    return images_base64[:3]  # limit