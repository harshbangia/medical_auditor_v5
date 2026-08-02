"""Case 180 (Madhu Sudan) — align software with NotebookLM / manual Glowix report."""

from __future__ import annotations

import unittest

from backend.ai.audit_result_enricher import enrich_audit_result
from backend.utils.claim_details_extractor import _pick_best_bill_amount
from backend.utils.clinical_fwa_signals import (
    build_case180_style_findings,
    extract_hospital_names,
    find_alcohol_withdrawal_meds,
    lipase_amylase_ratio,
)
from backend.utils.demographics_normalizer import normalize_policy_number
from backend.utils.fraud_abuse_detector import detect_fraud_abuse


CASE180_TEXT = """
Patient Madhu Sudan, 38/M. Policy No: H1522712. Claim 2026032400172.
Prior admission at Jaswant Rai Speciality Hospital from 07-03-2026 to 15-03-2026
for Acute Pancreatitis with Sepsis. Current hospitalization at Dhanvantri Hospital
DOA 23-03-2026 DOD 28-03-2026. Diagnosis: Acute Pancreatitis with peripancreatic
necrotic collection (WON).

Discharge summary history of alcoholism: No. Claim form alcohol: No.
MRCP: Gall bladder sludge.

Pharmacy / nursing charts: Librium 10, Petril MD, Oliza-5, Zolfresh 10, Inderal LA 40.
Also Merotec 1GM, Zutig 50MG, Dalacin C, Creon 25000, Rabifast IV INJ billed repeatedly.

Lab: Serum Amylase 930, Serum Lipase 5896.00, GGT 50. Potassium 3.0 then 2.9.

Health Claim Assessor Report: Bill Amount Verification failed aggregate-sum check.
Pharmacy bill A021042 grand total 7801.00 but line-item sum only 154.
Bill A021314 grand total 8120.00 vs blank. Final Bill 104005 amount 42130.
Claimed Amount 80800. Total billed amount 109355.83.
Mathematically impossible supply billing flagged.
"""


class TestCase180PolicyAndBill(unittest.TestCase):
    def test_policy_ocr_hi_to_h15(self):
        self.assertEqual(normalize_policy_number("HI5522712"), "H1522712")
        self.assertEqual(normalize_policy_number("H1522712"), "H1522712")

    def test_pharmacy_8120_loses_to_final_bill(self):
        candidates = [
            ("Rs. 8,120", 80, "pharmacy_A021314.pdf", "pharmacy"),
            ("Rs. 42,130", 90, "final_bill.pdf", "bill"),
            ("Rs. 80,800", 95, "assessor.pdf", "claim"),
        ]
        value, _src = _pick_best_bill_amount(candidates)
        digits = value.replace(",", "").replace(" ", "")
        self.assertTrue(
            "80800" in digits or "42130" in digits,
            msg=f"unexpected bill pick: {value}",
        )
        self.assertNotIn("8120", digits.replace("80800", "").replace("42130", ""))


class TestCase180AlcoholSignals(unittest.TestCase):
    def test_detects_withdrawal_meds_and_ratio(self):
        meds = find_alcohol_withdrawal_meds(CASE180_TEXT)
        self.assertGreaterEqual(len(meds), 4)
        ratio = lipase_amylase_ratio(CASE180_TEXT)
        self.assertIsNotNone(ratio)
        self.assertGreaterEqual(ratio[2], 6.0)

    def test_hospitals(self):
        names = extract_hospital_names(CASE180_TEXT)
        self.assertIn("Jaswant Rai Speciality Hospital", names)
        self.assertIn("Dhanvantri Hospital", names)

    def test_fwa_findings_high(self):
        findings = build_case180_style_findings(
            CASE180_TEXT,
            {"claim_details": {"diagnosis": "Acute Pancreatitis with necrotic collection (WON)"}},
        )
        inds = " ".join(f["indicator"] for f in findings).lower()
        self.assertIn("alcohol-withdrawal", inds)
        self.assertIn("lipase/amylase", inds)
        self.assertIn("multi-hospital", inds)
        self.assertIn("pharmacy bill", inds)


class TestCase180Enrichment(unittest.TestCase):
    def test_enrich_sets_repudiate(self):
        result = {
            "patient_details": {"name": "Mr. Madhu Sudan", "age": "38", "sex": "Male"},
            "insurance_details": {
                "insurance_company": "IFFCO-Tokio General Insurance Company Limited",
                "policy_number": "HI5522712",
                "claim_incident_number": "2026032400172",
            },
            "claim_details": {
                "hospital": "DHANVANTRI HOSPITAL",
                "date_of_admission": "03/03/2026",
                "diagnosis": "Acute Pancreatitis with peripancreatic necrotic collection (WON)",
                "procedure_or_surgery": "Medical Management",
                "total_hospital_bill": "Rs. 8,120",
            },
            "observations": [
                {
                    "question": "The present diagnosis can be co related to alcoholic pancreatitis or not?",
                    "answer": "Not Supported",
                    "analysis": "Cannot be correlated to alcoholic pancreatitis; no evidence of alcohol use.",
                }
            ],
            "auditor_conclusion": (
                "Based on available documents, this is an emergency admission for large "
                "intraparenchymal hemorrhage with ICU management. Claim recommended."
            ),
            "claim_recommended": "Yes",
            "compliance_verdict": "Compliant",
            "treatment_billing_audit": {},
            "financial_review": {},
        }
        out = enrich_audit_result(result, CASE180_TEXT)
        self.assertEqual(normalize_policy_number(out["insurance_details"]["policy_number"]), "H1522712")
        self.assertEqual(out.get("claim_recommended"), "No")
        self.assertRegex(out.get("auditor_conclusion") or "", r"(?i)repudiat|exclusion")
        self.assertNotRegex(out.get("auditor_conclusion") or "", r"(?i)intraparenchymal")
        hospital = str((out.get("claim_details") or {}).get("hospital") or "")
        self.assertRegex(hospital, r"(?i)jaswant")
        self.assertRegex(hospital, r"(?i)dhanvantri")
        qs = " ".join(
            str(o.get("question") or "") + str(o.get("analysis") or "")
            for o in (out.get("observations") or [])
            if isinstance(o, dict)
        )
        self.assertRegex(qs, r"(?i)alcoholic pancreatitis")
        self.assertNotRegex(qs, r"(?i)cannot be correlated to alcoholic")
        fa = detect_fraud_abuse(CASE180_TEXT, out)
        self.assertEqual(fa["risk_level"], "High")


if __name__ == "__main__":
    unittest.main()
