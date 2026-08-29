"""Assessor-first seal, OCR name twins, and deep narrative wiring."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.notebook.assessor_parser import parse_assessor_text
from backend.notebook.contradictions import _names_equivalent, detect_foreign_patient_names
from backend.notebook.identity_seal import apply_identity_seal, build_identity_seal
from backend.notebook.models import NotebookChunk


ASSESSOR_BHAGYASHRI = """
Health Claim Assessor Report
Claim Number: 20260708000052
Sub Claim Number: 20260708000052.R1
Policy Number: H1684623
Name of The Insured: BHAGYASHRI VITTHAL TATKARE
Gender: Female
Date of Birth: 12/04/1968
Age: 58
Claimed Amount: 50000.0
Hospital Name: Wellness clinic And Shri Bansidhar Agrwal Hospital
Date of Admission: 06/07/2026
Date of Discharge: 11/07/2026
FWA Alert: 5 out of 8 require to be checked
MRC Alert: 4 out of 7
"""


class TestAssessorDemographics(unittest.TestCase):
    def test_parses_age_dob_dates_claim(self):
        parsed = parse_assessor_text(ASSESSOR_BHAGYASHRI, "Health Claim Assessor Report.pdf")
        self.assertTrue(parsed["is_assessor"])
        self.assertEqual(parsed["claim_number"], "20260708000052")
        self.assertEqual(parsed["policy_number"], "H1684623")
        self.assertIn("BHAGYASHRI", parsed["patient_name"].upper())
        self.assertEqual(parsed["age"], "58")
        self.assertEqual(parsed["date_of_birth"], "12/04/1968")
        self.assertEqual(parsed["sex"], "Female")
        self.assertEqual(parsed["date_of_admission"], "06/07/2026")
        self.assertEqual(parsed["date_of_discharge"], "11/07/2026")
        self.assertEqual(parsed["claimed_amount"], "50000.0")


class TestSealDemographics(unittest.TestCase):
    def test_seal_overwrites_child_age_and_na_claim(self):
        seal = build_identity_seal(
            corpus_text=ASSESSOR_BHAGYASHRI + "\nTotal Bill Amount: 79324\n",
            assessor=parse_assessor_text(ASSESSOR_BHAGYASHRI, "assessor.pdf"),
            admission_yyyymmdd="20260706",
            expected_patient_name="Bhagyashri Vitthal Tatkare",
        )
        self.assertEqual(seal.claim_incident_number, "20260708000052")
        self.assertEqual(seal.policy_number, "H1684623")
        self.assertEqual(seal.age, "58")
        self.assertIn("79,324", seal.total_hospital_bill.replace(" ", ""))

        result = {
            "patient_details": {"name": "Wrong", "age": "3", "sex": "Female"},
            "insurance_details": {"claim_incident_number": "NA", "policy_number": ""},
            "claim_details": {
                "diagnosis": "Not clearly documented",
                "total_hospital_bill": "Rs. 3,146.18",
            },
            "financial_review": {"total_hospital_bill": "Rs. 3,146.18"},
            "claim_recommended": "Yes",
        }
        apply_identity_seal(result, seal, force_zero_recommended_if_rejected=False)
        self.assertEqual(result["patient_details"]["age"], "58")
        self.assertIn("BHAGYASHRI", result["patient_details"]["name"].upper())
        self.assertEqual(result["insurance_details"]["claim_incident_number"], "20260708000052")
        self.assertIn("79,324", result["financial_review"]["total_hospital_bill"].replace(" ", ""))


class TestNameOcrTwins(unittest.TestCase):
    def test_tatkare_variants_equivalent(self):
        self.assertTrue(
            _names_equivalent("Bhagyashri Vitthal Tatkare", "Bhagyshree Tarkate")
        )
        self.assertTrue(
            _names_equivalent("Bhagyashri Vitthal Tatkare", "Bhagyashree Tatkane")
        )

    def test_ocr_variant_is_low_not_high(self):
        chunks = [
            NotebookChunk(
                chunk_id="c1",
                doc_id="bill",
                filename="bill.pdf",
                page=1,
                doc_type="bill",
                text="Patient Name: Bhagyshree Tatkare\nAge 58",
            )
        ]
        findings = detect_foreign_patient_names(chunks, "Bhagyashri Vitthal Tatkare")
        # Equivalent → no mismatch finding
        self.assertEqual(findings, [])


class TestDeepNarrative(unittest.TestCase):
    def test_deepen_replaces_shallow_observations(self):
        from backend.notebook.deep_narrative import deepen_observations

        result = {
            "patient_details": {"name": "Bhagyashri", "age": "58", "sex": "Female"},
            "claim_details": {"diagnosis": "Bronchitis", "hospital": "Wellness"},
            "insurance_details": {"claim_incident_number": "20260708000052"},
            "financial_review": {"total_hospital_bill": "Rs. 79,324"},
            "observations": [
                {"question": "Is admission justified?", "answer": "Insufficient Evidence", "analysis": "short"}
            ],
        }
        mock_raw = """{
          "observations": [
            {
              "question": "Does clinical severity justify ICU admission?",
              "answer": "Insufficient Evidence",
              "analysis": "Assessor and discharge card document bronchitis with comorbidities. Source: Health Claim Assessor Report. Vitals and CURB-65 are not clearly transcribed in the available corpus, so medical necessity of ICU stay cannot be fully verified without indoor papers."
            },
            {
              "question": "Are identity details consistent?",
              "answer": "Partially Supported",
              "analysis": "Assessor lists BHAGYASHRI VITTHAL TATKARE with DOB 12/04/1968. Hospital bill OCR variants (Bhagyshree Tarkate) are spelling variants of the same insured and should not alone trigger repudiation."
            }
          ],
          "auditor_observation_summary": "Identity sealed; documentation gaps on vitals remain.",
          "conclusion": "Query for complete indoor papers before final recommendation."
        }"""
        with patch("backend.notebook.deep_narrative.get_llm_provider") as gp:
            provider = MagicMock()
            provider.complete.return_value = mock_raw
            gp.return_value = provider
            with patch("backend.notebook.deep_narrative.model_for", return_value="gemini-test"):
                out = deepen_observations(
                    result,
                    corpus_text="=== Source document: Health Claim Assessor Report ===\n" + ASSESSOR_BHAGYASHRI,
                    guideline_text="CURB-65 scoring for pneumonia severity.",
                )
        self.assertEqual(len(out["observations"]), 2)
        self.assertIn("Assessor", out["observations"][0]["analysis"])
        self.assertTrue(out.get("deep_narrative", {}).get("applied"))


if __name__ == "__main__":
    unittest.main()
