"""Tests for Glowix Expert Opinion proforma PDF generation."""

import os
import tempfile
import unittest

from backend.utils.glowix_proforma_pdf import generate_glowix_expert_opinion_pdf
from backend.utils.pdf_generator import generate_pdf


SAMPLE_REPORT = {
    "report_date": "20-07-2026",
    "patient_details": {
        "name": "Mrs. Durga Devi",
        "age": "49",
        "sex": "Female",
    },
    "insurance_details": {
        "insurance_company": "Iffco Tokio General Insurance",
        "policy_number": "H1685201",
        "policy_period": "01/01/2026 to 31/12/2026",
        "claim_incident_number": "2026071800281",
    },
    "claim_details": {
        "hospital": "Gokuldas Hospital Pvt. Ltd.",
        "date_of_admission": "18/07/2026",
        "date_of_discharge": "20/07/2026",
        "nature_of_admission": "Emergency",
        "diagnosis": "Mild L3 Compression Fracture",
        "procedure_or_surgery": "Medical management",
        "total_hospital_bill": "100000",
    },
    "clinical_findings": [
        {"parameter": "Hemoglobin", "value": "12.1", "normal_range": "12-15", "comment": "Normal"},
    ],
    "timeline": [
        {"date": "18/07/2026", "event": "Admitted"},
        {"date": "20/07/2026", "event": "Discharged"},
    ],
    "clinical_checklist": [
        {"area": "Indoor Case Papers", "available": "YES", "remarks": ""},
        {"area": "Lab / Radiology", "available": "YES", "remarks": "X-ray/MRI"},
    ],
    "document_sources": [
        {"filename": "indoor.pdf"},
        {"filename": "mri_report.pdf"},
    ],
    "observations": [
        {
            "question": "Whether the fracture is acute or secondary to spondylotic changes",
            "analysis": "The fracture is acute. Osteoporotic changes are degenerative and do not explain the acute presentation after fall.",
            "answer": "Supported",
        },
        {
            "question": "Is hospitalization required or can this be managed OPD",
            "analysis": "Admission justified due to immobility and need for IV therapy with close monitoring.",
            "answer": "Supported",
        },
    ],
    "billing_disallowances": [
        {
            "title": "Non-payable consumables",
            "amount": "5000",
            "reason": "IRDAI List I items billed",
            "evidence": "Pharmacy bill gloves/syringes",
            "audit_action": "Deduct Rs. 5,000",
        }
    ],
    "documentation_gaps": [
        {
            "title": "Missing MRI film copy",
            "finding": "Report present but film not enclosed",
            "evidence": "Radiology folder",
            "audit_action": "Query hospital for films",
        }
    ],
    "inference": (
        "Mrs. Durga Devi, 49 years female, admitted after fall with L3 compression fracture. "
        "Admission is justified for medical management."
    ),
    "auditor_conclusion": "Admission justified; deduct non-payable consumables.",
    "auditor_observation_summary": "Acute L3 compression fracture with medically necessary short admission.",
    "compliance_verdict": "Compliant",
    "claim_recommended": "Yes",
    "treatment_billing_audit": {},
    "financial_review": {"total_hospital_bill": "100000", "non_payable_amount": "5000"},
}


class GlowixProformaPdfTests(unittest.TestCase):
    def test_generates_nonempty_pdf(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            out = generate_glowix_expert_opinion_pdf(SAMPLE_REPORT, path)
            self.assertEqual(out, path)
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 1500)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_generate_pdf_uses_proforma(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            generate_pdf(SAMPLE_REPORT, path)
            self.assertGreater(os.path.getsize(path), 1500)
            import fitz
            doc = fitz.open(path)
            text = "\n".join(page.get_text("text") for page in doc)
            self.assertIn("Medical Audit Report", text)
            self.assertIn("GLOWIX", text)
            self.assertIn("Mrs. Durga Devi", text)
            self.assertIn("H1685201", text)
            self.assertIn("1. Patient Details", text)
            self.assertIn("6. Observations", text)
            self.assertNotIn("Q1.", text)
            self.assertNotIn("Ans.", text)
            self.assertGreaterEqual(doc.page_count, 1)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
