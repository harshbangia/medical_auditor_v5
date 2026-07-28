"""Regression tests for Case55-style OCR / handwriting demographics failures."""

from __future__ import annotations

import unittest

from backend.utils.demographics_normalizer import (
    extract_typed_demographics,
    filter_past_history_procedures,
    normalize_age,
    normalize_bill_amount,
    normalize_hospital_name,
    normalize_patient_name,
    normalize_policy_number,
    sanitize_mapped_facts,
)
from backend.utils.pdf_reader import _is_header_only_overlay
from backend.utils.case_facts_ledger import build_case_facts_ledger, merge_patient_from_ledger
from backend.utils.claim_details_extractor import _classify_document
from backend.utils.insurance_extractor import _is_valid_policy_number
from backend.ai.audit_result_enricher import _sanitize_demographics


CASE55_HIS_BANNER = (
    "Patient Name : Mr GAGANDEEP SINGH GULATI UHID : LMH2025435121 "
    "Age : 49 Y 0 M 0 D Gender : Male"
)

CASE55_LETTERHEAD = (
    "L. N. Medical College & J. K. Hospital, Bhopal\n"
    + CASE55_HIS_BANNER
    + "\nDiagnosis: ICH\nProposed Treatment: EVD\n"
    "H/O TURP\nSum Total Expected Cost: Rs. 1,33,000\n"
    "Policy No: H7583101\n"
)


class TestDemographicsNormalizer(unittest.TestCase):
    def test_age_rejects_149(self):
        self.assertEqual(normalize_age("149"), "")
        self.assertEqual(normalize_age("49"), "49")
        self.assertEqual(normalize_age("49 Y 0 M 0 D"), "49")

    def test_name_fixes_gaga_deep(self):
        self.assertEqual(
            normalize_patient_name("GaGa DEEP SINGH"),
            "Gagandeep Singh",
        )
        typed = extract_typed_demographics(CASE55_HIS_BANNER)
        self.assertIn("Gagandeep", typed["patient_name"])
        self.assertEqual(typed["age"], "49")
        self.assertEqual(typed["sex"], "Male")
        self.assertTrue(typed["uhid"].startswith("LMH"))

    def test_hospital_rejects_certified(self):
        self.assertEqual(normalize_hospital_name("Certified Hospital"), "")
        self.assertIn(
            "Hospital",
            normalize_hospital_name("L. N. Medical College & J. K. Hospital"),
        )

    def test_uhid_not_policy(self):
        self.assertEqual(normalize_policy_number("LMH2025435121"), "")
        self.assertEqual(normalize_policy_number("H7583101"), "H7583101")
        self.assertFalse(_is_valid_policy_number("LMH2025435121"))
        self.assertTrue(_is_valid_policy_number("H7583101"))

    def test_bill_rejects_rs_20(self):
        self.assertEqual(normalize_bill_amount("20"), "")
        self.assertEqual(normalize_bill_amount("Rs. 20"), "")
        self.assertIn(
            "133,000",
            normalize_bill_amount("", "Sum Total Expected Cost: Rs. 1,33,000"),
        )

    def test_past_history_turp_filtered(self):
        procs = filter_past_history_procedures(
            ["EVD", "TURP"],
            "Proposed Treatment: EVD\nPast History: H/O TURP\n",
        )
        self.assertIn("EVD", procs)
        self.assertNotIn("TURP", procs)

    def test_sanitize_mapped_facts_case55(self):
        bad = {
            "patient_name": "GaGa DEEP",
            "age": "149",
            "hospital": "Certified Hospital",
            "policy_number": "LMH2025435121",
            "bill_amount": "20",
            "procedures": ["EVD", "TURP"],
        }
        out = sanitize_mapped_facts(bad, CASE55_LETTERHEAD)
        self.assertEqual(out["age"], "49")
        self.assertIn("Gagandeep", out["patient_name"])
        self.assertIn("J. K. Hospital", out["hospital"])
        self.assertEqual(out["policy_number"], "")
        self.assertIn("133,000", out["bill_amount"])
        self.assertNotIn("TURP", out["procedures"])


class TestHeaderOnlyVision(unittest.TestCase):
    def test_his_banner_forces_vision(self):
        self.assertTrue(_is_header_only_overlay(CASE55_HIS_BANNER))

    def test_full_typed_page_not_header_only(self):
        long = CASE55_HIS_BANNER + "\n" + ("Clinical note. " * 40)
        self.assertFalse(_is_header_only_overlay(long))


class TestLedgerAndEnricher(unittest.TestCase):
    def test_ledger_prefers_clean_name_and_age(self):
        per_doc = [
            {
                "source_file": "preauth.pdf",
                "document_type": "preauth",
                "patient_name": "GaGa DEEP",
                "age": "149",
                "hospital": "Certified Hospital",
                "policy_number": "LMH2025435121",
                "bill_amount": "20",
                "procedures": ["TURP"],
            },
            {
                "source_file": "assessment.pdf",
                "document_type": "clinical",
                "patient_name": "Mr Gagandeep Singh Gulati",
                "age": "49",
                "hospital": "L. N. Medical College & J. K. Hospital",
                "policy_number": "H7583101",
                "bill_amount": "Rs. 133000",
                "procedures": ["EVD"],
            },
        ]
        ledger = build_case_facts_ledger(per_doc, {})
        merged = ledger["merged"]
        self.assertEqual(merged["age"], "49")
        self.assertIn("Gagandeep", merged["patient_name"])
        self.assertIn("Hospital", merged["hospital"])
        self.assertEqual(merged["policy_number"], "H7583101")
        self.assertNotEqual(merged["bill_amount"], "")

        result = {
            "patient_details": {"name": "GaGa DEEP", "age": "149", "sex": ""},
            "claim_details": {"hospital": "Certified Hospital", "procedure_or_surgery": "EVD; TURP"},
            "insurance_details": {"policy_number": "LMH2025435121"},
        }
        merge_patient_from_ledger(result, ledger)
        _sanitize_demographics(result, CASE55_LETTERHEAD)
        self.assertEqual(result["patient_details"]["age"], "49")
        self.assertIn("Gagandeep", result["patient_details"]["name"])
        self.assertIn("Hospital", result["claim_details"]["hospital"])
        self.assertNotEqual(result["insurance_details"]["policy_number"], "LMH2025435121")

    def test_icps_classified_as_indoor(self):
        self.assertEqual(
            _classify_document("ICPS.pdf", "INDOOR CASE PAPER\nWard / Bed No: ICU-2"),
            "indoor_case",
        )


if __name__ == "__main__":
    unittest.main()
