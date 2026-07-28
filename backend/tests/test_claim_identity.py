"""Tests for claim-identity / insurer canonicalization / placeholder overwrite."""

from __future__ import annotations

import unittest

from backend.agents.claim_identity_agent import (
    _normalize_identity,
    apply_claim_identity_to_facts,
)
from backend.ai.audit_result_enricher import _sanitize_demographics
from backend.utils.demographics_normalizer import sanitize_mapped_facts
from backend.utils.insurance_extractor import (
    canonicalize_insurer_name,
    extract_insurance_from_text,
    merge_insurance_into_result,
)


CASE55_HIS = (
    "Patient Name : Mr GAGANDEEP SINGH GULATI UHID : LMH2025435121 "
    "Age : 49 Y 0 M 0 D Gender : Male"
)


class TestInsurerCanonicalize(unittest.TestCase):
    def test_yokio_becomes_iffco_tokio(self):
        self.assertIn(
            "IFFCO",
            canonicalize_insurer_name("YOKIO GENERAL INSURANCE COMPANY LIMITED"),
        )
        self.assertIn("Tokio", canonicalize_insurer_name("IFFCO TOKIO"))

    def test_policy_from_insured_id_line(self):
        text = (
            "REQUEST FOR CASHLESS HOSPITALIZATION\n"
            "IFFCO-TOKIO GENERAL INSURANCE COMPANY LIMITED\n"
            "Insured ID Number: H7583101-2-0\n"
            "Policy Number: H7583101\n"
        )
        facts = extract_insurance_from_text(text, source="PREAUTH.pdf")
        self.assertEqual(facts["policy_number"], "H7583101")
        self.assertIn("IFFCO", facts["insurance_company"].upper())


class TestAgeNotFromInsuredId(unittest.TestCase):
    def test_typed_his_age_overrides_age_2(self):
        out = sanitize_mapped_facts(
            {"patient_name": "X", "age": "2", "hospital": "", "policy_number": "H7583101-2-0"},
            CASE55_HIS,
        )
        self.assertEqual(out["age"], "49")

    def test_sanitize_demographics_prefers_his_age(self):
        result = {
            "patient_details": {"name": "Gagandeep", "age": "2", "sex": "Male"},
            "claim_details": {"hospital": "Islekar Hospital"},
            "insurance_details": {
                "insurance_company": "YOKIO GENERAL INSURANCE COMPANY LIMITED",
                "policy_number": "Not Provided",
            },
        }
        _sanitize_demographics(result, CASE55_HIS + "\nL. N. Medical College & J. K. Hospital\n")
        self.assertEqual(result["patient_details"]["age"], "49")


class TestMergePlaceholders(unittest.TestCase):
    def test_not_provided_overwritten_by_real_policy(self):
        result = {
            "insurance_details": {
                "insurance_company": "YOKIO GENERAL INSURANCE COMPANY LIMITED",
                "policy_number": "Not Provided",
                "claim_incident_number": "",
            }
        }
        merge_insurance_into_result(
            result,
            {
                "insurance_company": "IFFCO-Tokio General Insurance Company Limited",
                "policy_number": "H7583101",
                "claim_incident_number": "",
            },
        )
        ins = result["insurance_details"]
        self.assertEqual(ins["policy_number"], "H7583101")
        self.assertIn("IFFCO", ins["insurance_company"])


class TestClaimIdentityNormalize(unittest.TestCase):
    def test_normalize_preauth_vision_json(self):
        raw = {
            "insurance_company": "IFFCO-TOKIO GENERAL INSURANCE COMPANY LIMITED",
            "policy_number": "H7583101",
            "insured_id": "H7583101-2-0",
            "claim_incident_number": "",
            "patient_name": "MR. Gagandeep Singh",
            "age_years": "49",
            "sex": "Male",
            "hospital": "LN Medical college & JK Hospital",
            "bill_amount": "Rs. 1,33,000",
        }
        norm = _normalize_identity(raw)
        self.assertEqual(norm["age"], "49")
        self.assertEqual(norm["policy_number"], "H7583101")
        self.assertIn("IFFCO", norm["insurance_company"])
        self.assertIn("Hospital", norm["hospital"])
        self.assertIn("133,000", norm["bill_amount"])

        ins, claim = apply_claim_identity_to_facts(norm, {}, {})
        self.assertEqual(ins["policy_number"], "H7583101")
        self.assertEqual(claim["hospital"], norm["hospital"])
        self.assertEqual(claim["_identity_age"], "49")


if __name__ == "__main__":
    unittest.main()
