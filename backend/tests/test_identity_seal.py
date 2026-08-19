"""Identity seal — DOA claim repair, policy OCR twins, bill placeholders."""

from __future__ import annotations

import unittest

from backend.notebook.identity_seal import (
    apply_identity_seal,
    build_identity_seal,
    parse_admission_yyyymmdd,
    resolve_bill_amount,
    resolve_claim_number,
    resolve_policy_number,
)


class TestDoaClaimRepair(unittest.TestCase):
    def test_parse_admission(self):
        self.assertEqual(parse_admission_yyyymmdd("17/07/2026"), "20260717")
        self.assertEqual(parse_admission_yyyymmdd("2026-07-17"), "20260717")

    def test_assessor_wrong_digits_repaired_by_doa(self):
        # Production Bency: Assessor OCR 2026077000347, DOA 17/07/2026 → 2026071700347
        claim, meta = resolve_claim_number(
            corpus="Claim Number: 2026077000347\nClaim Number: 2026077000347\n",
            assessor={"claim_number": "2026077000347", "sub_claim_number": "2026077000347.R1"},
            admission_yyyymmdd="20260717",
        )
        self.assertEqual(claim, "2026071700347")
        self.assertTrue(any("doa_repair" in s for s in meta.get("sources") or []))


class TestPolicyConfusion(unittest.TestCase):
    def test_assessor_8_vs_attested_6(self):
        corpus = (
            "Policy Number: H1677879\n"
            "Member Code: H1677679-1-1\n"
            "Policy No H1677679\n"
        )
        pol, _ = resolve_policy_number(
            corpus,
            assessor={"policy_number": "H1677879"},
        )
        self.assertEqual(pol, "H1677679")


class TestBillPlaceholder(unittest.TestCase):
    def test_assessor_claimed_beats_50k(self):
        bill, _ = resolve_bill_amount(
            "Claimed Amount: 80800",
            assessor={"claimed_amount": "80800"},
            current_bill="Rs. 50,000",
        )
        self.assertIn("80,800", bill.replace(" ", ""))


class TestPackMismatch(unittest.TestCase):
    def test_chandra_must_not_get_bency_ids(self):
        from backend.notebook.builder import apply_notebook_to_result, build_case_notebook

        corpus = """
=== Source document: Health Claim Assessor Report d_11zon.pdf ===
Health Claim Assessor Report
Claim Number: 2026077000347
Sub Claim Number: 2026077000347.R1
Policy Number: H1677879
Name of The Insured: BENCY BIJU
Claimed Amount: 80800
Member Code: H1677679-1-1
FWA Alerts Identity Check SAVITHA A G
Date of Admission: 17/07/2026
"""
        nb = build_case_notebook(
            case_text=corpus,
            expected_patient_name="Chandra Kant Upadhyay",
            current_claim="2026077000347",
            current_policy="H1677879",
            admission_date="17/07/2026",
        )
        self.assertTrue(
            any("different patient" in str(f.get("indicator") or "").lower() for f in nb.fwa_findings)
        )
        result = {
            "patient_details": {"name": "Chandra Kant Upadhyay", "age": "55", "sex": "Male"},
            "insurance_details": {
                "claim_incident_number": "2026071700347",
                "policy_number": "H1677679",
            },
            "claim_details": {
                "date_of_admission": "17/07/2026",
                "total_hospital_bill": "Rs. 118,591",
                "diagnosis": "SOME OTHER DX",
            },
            "financial_review": {
                "total_hospital_bill": "Rs. 118,591",
                "recommended_approval_amount": "100000",
            },
            "fraud_abuse": {"findings": []},
            "claim_recommended": "Yes",
            "observations": [],
        }
        out = apply_notebook_to_result(result, nb)
        self.assertTrue((out.get("pack_integrity") or {}).get("ok") is False)
        self.assertEqual(out["insurance_details"].get("claim_incident_number"), "")
        self.assertEqual(out["insurance_details"].get("policy_number"), "")
        self.assertNotEqual(out.get("claim_details", {}).get("diagnosis"), "RIGHT HEMISPHERIC TRANSIENT ISCHEMIC ATTACK")
        self.assertEqual(out.get("claim_recommended"), "No")
        self.assertIn("BENCY", out.get("auditor_conclusion") or "")


class TestSealApply(unittest.TestCase):
    def test_full_bency_seal(self):
        corpus = """
Health Claim Assessor Report
Claim Number: 2026077000347
Sub Claim Number: 2026077000347.R1
Policy Number: H1677879
Name of The Insured: BENCY BIJU
Claimed Amount: 80800
Member Code : H1677679-1-1
Date of Admission: 17/07/2026
"""
        seal = build_identity_seal(
            corpus_text=corpus,
            assessor={
                "claim_number": "2026077000347",
                "sub_claim_number": "2026077000347.R1",
                "policy_number": "H1677879",
                "claimed_amount": "80800",
                "patient_name": "BENCY BIJU",
            },
            admission_yyyymmdd="20260717",
            current_claim="2026077000347",
            current_policy="H1677879",
            current_bill="Rs. 50,000",
            expected_patient_name="Mrs. Bency Biju",
        )
        self.assertFalse(seal.pack_mismatch)
        self.assertEqual(seal.claim_incident_number, "2026071700347")
        self.assertEqual(seal.policy_number, "H1677679")
        self.assertIn("80,800", seal.total_hospital_bill.replace(" ", ""))

        result = {
            "insurance_details": {
                "claim_incident_number": "2026077000347",
                "policy_number": "H1677879",
            },
            "claim_details": {"total_hospital_bill": "Rs. 50,000"},
            "financial_review": {
                "total_hospital_bill": "Rs. 50,000",
                "recommended_approval_amount": "45000",
                "net_claimable_amount": "Rs. 50,000",
            },
            "claim_recommended": "No",
            "claim_not_recommended": "Yes",
        }
        out = apply_identity_seal(result, seal)
        self.assertEqual(out["insurance_details"]["claim_incident_number"], "2026071700347")
        self.assertEqual(out["insurance_details"]["policy_number"], "H1677679")
        self.assertIn("80,800", out["financial_review"]["total_hospital_bill"].replace(" ", ""))
        self.assertEqual(out["financial_review"]["recommended_approval_amount"], "Rs. 0")


if __name__ == "__main__":
    unittest.main()
