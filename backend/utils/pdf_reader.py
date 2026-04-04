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
            text += page.get_text()

        doc.close()

    except Exception as e:
        print("⚠️ PyMuPDF failed:", str(e))

    # 🔥 ALWAYS RUN OCR (FOR RELIABILITY)
    try:
        from pdf2image import convert_from_path
        import pytesseract

        def extract_text_from_pdf(pdf_path):
            import fitz

            text = ""

            try:
                doc = fitz.open(pdf_path)

                for page in doc:
                    page_text = page.get_text()
                    text += page_text

                doc.close()

            except Exception as e:
                print("⚠️ PyMuPDF failed:", str(e))

            # 🔥 SMART DECISION (NOT LIMIT)
            if len(text.strip()) > 1000:
                print("✅ Using native PDF text (sufficient)")
                return text

            print("⚠️ Low text detected → using OCR...")

            # 🔥 OCR ONLY WHEN NEEDED
            try:
                from pdf2image import convert_from_path
                import pytesseract

                images = convert_from_path(pdf_path)

                ocr_text = ""
                for img in images:
                    ocr_text += pytesseract.image_to_string(img)

                return text + "\n" + ocr_text

            except Exception as e:
                print("❌ OCR FAILED:", str(e))

            return text

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

def extract_text_and_images(pdf_path):
    from pdf2image import convert_from_path
    import pytesseract

    text = ""
    images_base64 = []

    print("⚠️ Running OCR (single pass)...")

    pages = convert_from_path(pdf_path)

    for i, img in enumerate(pages):
        print(f"🧠 OCR page {i+1}/{len(pages)}")

        # TEXT
        text += pytesseract.image_to_string(img)

        # IMAGE (convert to base64 for model)
        import base64
        from io import BytesIO

        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue()).decode()

        images_base64.append(img_str)

    return text, images_base64