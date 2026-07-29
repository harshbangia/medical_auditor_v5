"""Regression tests for Case55 client deficiency-sheet feedback."""

from __future__ import annotations

import unittest

from backend.ai.audit_result_enricher import (
    _ensure_inference_and_summary,
    seed_deficiency_observations,
)
from backend.utils.claim_details_extractor import (
    _extract_clinical_diagnosis,
    _looks_like_ocr_garbage,
    _pick_best_bill_amount,
    _score_hospital_name,
)
from backend.utils.document_analysis import _summary_grounded
from backend.utils.demographics_normalizer import normalize_hospital_name, normalize_patient_name
from backend.utils.insurance_extractor import _extract_claim_incident


class TestHospitalGarbage(unittest.TestCase):
    def test_rejects_query_letter_ocr_as_hospital(self):
        junk = "te provide canteations forthe queries ese earn ths hospital"
        self.assertTrue(_looks_like_ocr_garbage(junk))
        self.assertLessEqual(_score_hospital_name(junk), 0)
        self.assertEqual(normalize_hospital_name(junk), "")


class TestDiagnosisSource(unittest.TestCase):
    def test_prefers_provisional_not_impression(self):
        text = (
            "Provisional diagnosis: Large Intraparenchymal hemorrhage Brain\n"
            "Impression: Right Thalamogeniculate bleed with IVH & brain stem bleed\n"
        )
        dx = _extract_clinical_diagnosis(text, "pre_auth")
        self.assertIn("Intraparenchymal", dx)
        self.assertNotIn("Thalamogeniculate", dx)

    def test_radiology_doc_returns_empty_diagnosis(self):
        text = "Impression: Right Thalamogeniculate bleed with heavy & brain stem bleed"
        self.assertEqual(_extract_clinical_diagnosis(text, "radiology"), "")


class TestBillPreferSumTotal(unittest.TestCase):
    def test_larger_preauth_total_wins(self):
        candidates = [
            ("Rs. 53,000", 90, "bill.pdf", "bill"),
            ("Rs. 1,33,000", 95, "PREAUTH.pdf", "pre_auth"),
        ]
        value, _src = _pick_best_bill_amount(candidates)
        digits = value.replace(",", "").replace(" ", "")
        self.assertIn("133000", digits)


class TestClaimIncident(unittest.TestCase):
    def test_extracts_13_digit_claim_id(self):
        text = "Claim Incident No.: 2025111700184\nPolicy No: H1583101"
        self.assertEqual(_extract_claim_incident(text), "2025111700184")


class TestNameAlias(unittest.TestCase):
    def test_bhagwandeep_to_gagandeep(self):
        self.assertIn("Gagandeep", normalize_patient_name("Mr Bhagwandeep Singh"))


class TestDocumentGrounding(unittest.TestCase):
    def test_rejects_meningioma_hallucination(self):
        summary = "OT notes show post operative diagnosis of intra parafalcine meningioma"
        source = "Operation notes EVD insertion for ICH. No tumor mentioned."
        self.assertFalse(_summary_grounded(summary, source))


class TestDeficiencyObservations(unittest.TestCase):
    def test_seeds_los_and_softens_deny(self):
        result = {
            "claim_details": {
                "diagnosis": "Large Intraparenchymal hemorrhage Brain",
                "hospital": "L.N. Medical College & J.K. Hospital",
            },
            "patient_details": {"name": "Gagandeep Singh Gulati", "age": "49", "sex": "Male"},
            "observations": [
                {
                    "question": "Are all documented patient names consistent across the case?",
                    "answer": "Not Supported",
                    "analysis": "Name mismatch",
                }
            ],
            "auditor_conclusion": "Recommend denying claim due to patient identity issues.",
            "compliance_verdict": "Non-Compliant",
            "report_summary": [
                "Patient Mr. Bhagwandeep Singh admitted to L.N. Medical College",
                "Hospital: te provide canteations forthe queries ese earn ths hospital",
            ],
        }
        case = (
            "Unconscious patient ICU large intraparenchymal hemorrhage. "
            "Inj Meropenem given. EVD planned."
        )
        seed_deficiency_observations(result, case)
        qs = " ".join(o["question"] for o in result["observations"])
        self.assertIn("extended duration", qs.lower())
        self.assertIn("meropenem", qs.lower())
        self.assertNotIn("patient names consistent", qs.lower())
        self.assertEqual(result["claim_recommended"], "Yes")
        self.assertNotRegex(result["auditor_conclusion"], r"(?i)deny")

        _ensure_inference_and_summary(result)
        summary = " ".join(result["report_summary"]).lower()
        self.assertNotIn("bhagwandeep", summary)
        self.assertNotIn("canteation", summary)
        self.assertIn("gagandeep", summary)


if __name__ == "__main__":
    unittest.main()
