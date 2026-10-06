"""Inspection-report mapping and GMS/FOR/DV/01 PDF."""

import os
import tempfile
import unittest

from backend.utils.inspection_mapper import map_audit_to_inspection
from backend.utils.inspection_proforma_pdf import generate_inspection_report_pdf
from backend.utils.pdf_generator import generate_pdf, resolve_report_kind
from backend.tests.test_glowix_proforma_pdf import SAMPLE_REPORT


def _pdf_text(path: str) -> str:
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def _write_inspection(data: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    generate_inspection_report_pdf(data, path)
    return path


COMPLETE_CASE = {
    "report_date": "20-07-2026",
    "compliance_verdict": "Non-Compliant",
    "inference": "Deny the claim because it is not medically necessary.",
    "auditor_conclusion": "Recommended approval amount should be withheld.",
    "patient_details": {
        "name": "Ravi Kumar",
        "age": "54",
        "sex": "Male",
        "hospital_reg_no": "UHID1001",
    },
    "insurance_details": {
        "insurance_company": "Test Insurer",
        "policy_number": "POL123",
        "claim_incident_number": "CLM999",
    },
    "claim_details": {
        "hospital": "City Hospital",
        "date_of_admission": "01/07/2026",
        "date_of_discharge": "04/07/2026",
        "diagnosis": "Fever",
        "procedure_or_surgery": "Medical management",
    },
    "document_sources": [
        {"filename": name}
        for name in (
            "claim_form.pdf",
            "admission.pdf",
            "progress_notes.pdf",
            "lab_report.pdf",
            "discharge_summary.pdf",
            "final_bill.pdf",
        )
    ],
    "timeline": [
        {"date": "01/07/2026", "event": "Admitted"},
        {"date": "04/07/2026", "event": "Discharged"},
    ],
    "documentation_gaps": [
        "IRDAI non-payable consumables should be deducted from the claim",
    ],
}


class InspectionMapperTests(unittest.TestCase):
    def test_sample_case_is_conforming_with_observation(self):
        mapped = map_audit_to_inspection(SAMPLE_REPORT)
        self.assertEqual(mapped["conformity_code"], "C-O")
        self.assertEqual(mapped["document_no"], "GMS/FOR/DV/01")
        self.assertIn("Mrs. Durga Devi", mapped["patient"]["name"])
        self.assertEqual(mapped["patient"]["claim_no"], "2026071800281")
        evidence = " ".join(row["evidence"] for row in mapped["discrepancies"])
        self.assertIn("film", evidence.lower())
        self.assertNotIn("irdai", evidence.lower())
        self.assertNotIn("medically necessary", mapped["conclusion_remarks"].lower())
        self.assertNotIn("deduct", mapped["conclusion_remarks"].lower())

    def test_medical_verdict_is_not_copied_into_conformity(self):
        mapped = map_audit_to_inspection(COMPLETE_CASE)
        self.assertEqual(mapped["conformity_code"], "C")
        self.assertNotIn("Deny the claim", mapped["conclusion_remarks"])
        self.assertNotIn("Non-Compliant", mapped["conformity_basis"])
        self.assertEqual(mapped["discrepancies"], [])

    def test_reversed_dates_are_nonconforming(self):
        data = dict(COMPLETE_CASE)
        data["claim_details"] = dict(COMPLETE_CASE["claim_details"])
        data["claim_details"]["date_of_admission"] = "10/07/2026"
        data["claim_details"]["date_of_discharge"] = "01/07/2026"
        data["documentation_gaps"] = []
        mapped = map_audit_to_inspection(data)
        self.assertEqual(mapped["overall_chronology"], "Material Discrepancy")
        self.assertEqual(mapped["conformity_code"], "NC")

    def test_empty_case_is_unable_to_conclude(self):
        mapped = map_audit_to_inspection({})
        self.assertEqual(mapped["conformity_code"], "UC")
        self.assertEqual(mapped["overall_completeness"], "Unable to Determine")

    def test_report_kind_defaults_to_medical(self):
        self.assertEqual(resolve_report_kind(None), "medical")
        self.assertEqual(resolve_report_kind("inspection"), "inspection")
        self.assertEqual(resolve_report_kind("qci"), "inspection")
        self.assertEqual(resolve_report_kind("something-else"), "medical")


class InspectionPdfTests(unittest.TestCase):
    def test_inspection_pdf_follows_proforma_and_omits_claim_opinion(self):
        path = _write_inspection(SAMPLE_REPORT)
        try:
            text = _pdf_text(path)
            self.assertIn("DOCUMENT VERIFICATION INSPECTION REPORT", text)
            self.assertIn("GMS/FOR/DV/01", text)
            self.assertIn("ISO/IEC 17020", text)
            self.assertIn("IAF SCOPE 35", text)
            self.assertIn("Mrs. Durga Devi", text)
            self.assertIn("2026071800281", text)
            self.assertIn("[X] C-O – CONFORMING WITH OBSERVATION", text)
            self.assertIn("[ ] C – CONFORMING", text)
            self.assertIn("does not by itself establish fraud, forgery", text)
            self.assertIn("Medical necessity, treatment appropriateness", text)
            self.assertIn("END OF REPORT", text)
            self.assertNotIn("Medical Audit Report", text)
            self.assertNotIn("Fraud risk", text)
            self.assertNotIn("Admission is justified", text)
            self.assertNotIn("Deduct", text)
            self.assertGreater(os.path.getsize(path), 1500)
        finally:
            os.remove(path)

    def test_medical_pdf_is_unchanged(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            generate_pdf(SAMPLE_REPORT, path)
            text = _pdf_text(path)
            self.assertIn("Medical Audit Report", text)
            self.assertNotIn("DOCUMENT VERIFICATION INSPECTION REPORT", text)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
