"""QR / barcode authenticity — decode, match to patient, surface in report."""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.services.document_agent_audit import _case_text_from_result
from backend.utils.barcode_qr_scanner import (
    apply_document_codes,
    build_document_codes_report,
    decoder_available,
    format_document_codes_block,
    match_hits_to_identity,
    parse_code_payload,
    scan_pdf,
)
from backend.utils.glowix_proforma_pdf import generate_glowix_expert_opinion_pdf


FELIX_QR = (
    "https://api.felixhospital.com/PDF_API/HIS/FetchReports?SERVICEID=&BILLNO=SER0414338"
)


class ParsePayloadTests(unittest.TestCase):
    def test_felix_his_url(self):
        parsed = parse_code_payload(FELIX_QR)
        self.assertEqual(parsed["kind"], "url")
        self.assertIn("felixhospital.com", parsed["host"])
        self.assertEqual(parsed["bill_no"], "SER0414338")
        self.assertEqual(parsed["issuer_hint"], "felix")

    def test_plain_accession(self):
        parsed = parse_code_payload("FHP260918175")
        self.assertEqual(parsed["kind"], "plain")
        self.assertEqual(parsed["bill_no"], "FHP260918175")


class RelatednessTests(unittest.TestCase):
    def _hit(self, **kwargs):
        parsed = parse_code_payload(kwargs.get("payload") or FELIX_QR)
        rec = {
            "source": "NIKHIL--1.pdf",
            "page": 2,
            "format": "QR Code",
            "payload": FELIX_QR,
            "printed_name": "Mr. NIKHIL KUMAR",
            "printed_uhid": "FHP260918175",
            "printed_bar_cd": "2609180812",
            "ocr_excerpt": "Name : Mr. NIKHIL KUMAR UMR NO FHP260918175 BAR CD : 2609180812",
            "related": "inconclusive",
            "reasons": [],
            **parsed,
        }
        rec.update(kwargs)
        return rec

    def test_felix_qr_matches_nikhil(self):
        hits = match_hits_to_identity(
            [self._hit()],
            {
                "patient_name": "Mr. NIKHIL KUMAR",
                "hospital": "Felix Hospital",
                "uhid": "FHP260918175",
            },
            case_text="BAR CD : 2609180812 IP No NIP260918042",
        )
        self.assertEqual(hits[0]["related"], "yes")
        reasons = " ".join(hits[0]["reasons"]).lower()
        self.assertIn("felix", reasons)
        self.assertIn("nikhil", reasons)

    def test_other_hospital_qr_is_mismatch(self):
        payload = "https://reports.maxhealthcare.in/verify?UHID=ZZZ999&NAME=RAJESH"
        parsed = parse_code_payload(payload)
        hit = self._hit(
            printed_name="RAJESH SHARMA",
            ocr_excerpt="RAJESH SHARMA",
            **parsed,
        )
        hits = match_hits_to_identity(
            [hit],
            {"patient_name": "Mr. NIKHIL KUMAR", "hospital": "Felix Hospital"},
        )
        self.assertEqual(hits[0]["related"], "no")

    def test_report_summary_and_block(self):
        report = build_document_codes_report(
            [self._hit()],
            identity={"patient_name": "NIKHIL KUMAR", "hospital": "Felix Hospital"},
            scanned_files=["NIKHIL--1.pdf"],
        )
        self.assertEqual(report["overall"], "related")
        self.assertTrue(report["related_to_patient"].lower().startswith("yes"))
        block = format_document_codes_block(report)
        self.assertIn("DOCUMENT QR / BARCODE AUDIT", block)
        self.assertIn("SER0414338", block)
        self.assertIn("NIKHIL", block)

    def test_mismatch_seeds_fwa(self):
        parsed = parse_code_payload("https://lab.otherhospital.com/r?BILLNO=X1")
        hit = self._hit(printed_name="OTHER PERSON", **parsed)
        result = {
            "patient_details": {"name": "NIKHIL KUMAR"},
            "claim_details": {"hospital": "Felix Hospital"},
            "fraud_abuse": {"findings": [], "risk_level": "Low"},
        }
        out = apply_document_codes(result, [hit])
        self.assertEqual(out["document_codes"]["overall"], "mismatch")
        indicators = [f["indicator"] for f in out["fraud_abuse"]["findings"]]
        self.assertTrue(any("QR" in i or "barcode" in i.lower() for i in indicators))
        self.assertEqual(out["fraud_abuse"]["risk_level"], "High")


class QrRoundtripTests(unittest.TestCase):
    @unittest.skipUnless(decoder_available(), "zxing-cpp not installed")
    def test_scan_generated_qr_pdf(self):
        import zxingcpp
        from PIL import Image
        import fitz

        barcode = zxingcpp.create_barcode(
            FELIX_QR, zxingcpp.BarcodeFormat.QRCode, ec_level="50%"
        )
        arr = barcode.to_image(scale=6)
        img = Image.fromarray(arr)
        pdf_path = tempfile.mkstemp(suffix=".pdf")[1]
        png_path = tempfile.mkstemp(suffix=".png")[1]
        try:
            img.save(png_path)
            doc = fitz.open()
            page = doc.new_page(width=400, height=400)
            page.insert_image(page.rect, filename=png_path)
            doc.save(pdf_path)
            doc.close()
            hits = scan_pdf(pdf_path, source_name="lab.pdf")
            payloads = [h["payload"] for h in hits]
            self.assertIn(FELIX_QR, payloads)
            self.assertEqual(hits[0]["bill_no"], "SER0414338")
        finally:
            for path in (png_path, pdf_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    @unittest.skipUnless(
        os.path.isfile(
            "/Users/harshbangia/Downloads/casenikhilkumar/NIKHIL--1.pdf"
        ),
        "Nikhil Kumar lab PDF not on this machine",
    )
    def test_nikhil_lab_pdf_decodes_felix_qr(self):
        hits = scan_pdf(
            "/Users/harshbangia/Downloads/casenikhilkumar/NIKHIL--1.pdf",
            source_name="NIKHIL--1.pdf",
        )
        payloads = [h.get("payload") or "" for h in hits]
        self.assertTrue(any("felixhospital.com" in p for p in payloads))
        self.assertTrue(any("SER0414338" in p for p in payloads))


class ReportSurfaceTests(unittest.TestCase):
    def test_pdf_and_ask_corpus_include_codes(self):
        data = {
            "report_date": "28-09-2026",
            "patient_details": {"name": "Mr. NIKHIL KUMAR", "age": "39", "sex": "Male"},
            "insurance_details": {
                "insurance_company": "IFFCO-TOKIO",
                "policy_number": "H0318887",
                "claim_incident_number": "2026091900347",
            },
            "claim_details": {
                "hospital": "Felix Hospital",
                "diagnosis": "LRTI",
                "date_of_admission": "18-09-2026",
                "date_of_discharge": "22-09-2026",
            },
            "document_codes": {
                "scanned": True,
                "overall": "related",
                "related_to_patient": "Yes — codes belong to this patient / claimed hospital",
                "summary": "16 QR codes decoded on NIKHIL--1.pdf. Issuer: api.felixhospital.com",
                "unique_codes": [
                    {
                        "format": "QR Code",
                        "payload": FELIX_QR,
                        "pages": ["NIKHIL--1.pdf p.2"],
                        "related": "yes",
                        "reasons": ["QR issuer matches Felix Hospital"],
                    }
                ],
            },
            "inference": "Admission justified with billing deductions.",
            "auditor_conclusion": "Partial pay.",
            "observations": [],
        }
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            generate_glowix_expert_opinion_pdf(data, path)
            import fitz

            text = "".join(page.get_text() for page in fitz.open(path))
            self.assertIn("Document QR / barcode authenticity", text)
            self.assertIn("SER0414338", text)
            self.assertIn("Related to this patient", text)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        corpus = _case_text_from_result(data)
        self.assertIn("DOCUMENT QR / BARCODE AUDIT", corpus)
        self.assertIn("SER0414338", corpus)


if __name__ == "__main__":
    unittest.main()
