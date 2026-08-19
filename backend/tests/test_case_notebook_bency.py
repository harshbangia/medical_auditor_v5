"""Case Notebook + Bency Bliju golden regressions (NotebookLM parity P0)."""

from __future__ import annotations

import unittest

from backend.notebook.assessor_parser import parse_assessor_text
from backend.notebook.builder import apply_notebook_to_result, build_case_notebook
from backend.notebook.contradictions import (
    detect_abg_vs_vitals,
    detect_diagnostic_only_admission,
    detect_foreign_patient_names,
    detect_ped_nondisclosure,
)
from backend.notebook.models import NotebookChunk
from backend.notebook.validators import (
    normalize_claim_incident,
    repair_claim_ocr_candidates,
    validate_ids_from_corpus,
)


BENCY_CORPUS = """
=== Source document: Health Claim Assessor Report d_11zon.pdf ===
Health Claim Assessor Report
Claim Number: 2026077000347
Sub Claim Number: 2026077000347.R1
Policy Number: H1677879
Name of The Insured: BENCY BIJU
Claimed Amount: 80800
Claim Type: Reimbursement
Hospital Name: Daya General Hospital Ltd
Member Code: H1677679-1-1
FWA Alerts Identity Check: clinical charts contain SAVITHA A G Patient ID 5309
demographic mismatches and record mixing. Bill Amount Verification failed aggregate-sum check.

=== Source document: indoor_notes.pdf ===
=== Page 1 — vision transcription (indoor_notes.pdf) ===
Patient Name: SAVITHA A G
Patient ID: 5309
Also chart header BENCY BUU
Pharmacy extract issued to DAYA THE
Claim Incident No: 2026077000347
Claim Incident No: 2026077000347
Policy No: H1677879
Date of Admission: 17/07/2026

=== Page 2 ===
ABG: pO2 42.3 mmHg sO2 73.7%
Nursing chart: conscious alert ambulant on room air SpO2 98%
Anion gap -22.7 mmol/L

=== Source document: preauth.pdf ===
Past medical history / chronic illness: NA
Discharge: known hypothyroidism on Thyronorm 175 mcg daily
Diagnosis: RIGHT HEMISPHERIC TRANSIENT ISCHEMIC ATTACK
MRI Brain: no evidence of acute infarct
MR Angiography: no evidence of occlusion
Medications: Aspirin, Clopidogrel, Atorvastatin
Admitted to ICU for 3 days for evaluation
Claim Incident: 2026077000347
Policy Number: H1677879
"""


class TestClaimRepair(unittest.TestCase):
    def test_bency_claim_ocr_prefers_2026(self):
        bad = "2020877700347"
        good = "2026071700347"
        picked = repair_claim_ocr_candidates([bad, good])
        self.assertEqual(picked.split(".")[0], "2026071700347")

    def test_majority_bad_year_loses_to_good_twin(self):
        picked = repair_claim_ocr_candidates([
            "2020877000347",
            "2020877000347",
            "2020877000347",
            "2020877000347",
            "2026071700347",
        ])
        self.assertEqual(picked.split(".")[0], "2026071700347")

    def test_assessor_wins_over_corrupt(self):
        ids = validate_ids_from_corpus(
            BENCY_CORPUS,
            assessor={
                "claim_number": "2026077000347",
                "policy_number": "H1677879",
                "claimed_amount": "80800",
            },
            current_claim="2026077000347",
            current_policy="H1677879",
            admission_date="17/07/2026",
        )
        self.assertEqual(ids["claim_incident_number"].split(".")[0], "2026071700347")
        self.assertEqual(ids["policy_number"], "H1677679")

    def test_policy_near_duplicate_prefers_assessor(self):
        from backend.notebook.validators import repair_policy_ocr_candidates
        picked = repair_policy_ocr_candidates(
            ["H1677879", "H1677879", "H1677679"],
            preferred="H1677679",
        )
        self.assertEqual(picked, "H1677679")


class TestAssessorParse(unittest.TestCase):
    def test_parse_claim_and_fwa(self):
        parsed = parse_assessor_text(BENCY_CORPUS, "Health Claim Assessor Report.pdf")
        self.assertTrue(parsed["is_assessor"])
        # Assessor OCR may be noisy; seal repairs via DOA — parser still extracts a claim
        self.assertTrue(parsed["claim_number"])
        self.assertTrue(any("Identity" in a["indicator"] for a in parsed["fwa_alerts"]))


class TestContradictions(unittest.TestCase):
    def _chunks(self):
        return [
            NotebookChunk("1", "d1", "indoor.pdf", 1, "indoor_case", BENCY_CORPUS),
            NotebookChunk("2", "d2", "preauth.pdf", 1, "pre_auth", BENCY_CORPUS),
        ]

    def test_foreign_name(self):
        findings = detect_foreign_patient_names(self._chunks(), "Bency Biju")
        blob = " ".join(f["evidence"] for f in findings).lower()
        self.assertTrue("savitha" in blob)
        self.assertNotIn("buu", blob)  # BENCY BUU is same-patient OCR, not foreign


    def test_abg_vs_spo2(self):
        findings = detect_abg_vs_vitals(self._chunks())
        self.assertTrue(findings)

    def test_thyroid_nondisclosure(self):
        findings = detect_ped_nondisclosure(self._chunks())
        self.assertTrue(any("hypothyroid" in f["indicator"].lower() for f in findings))

    def test_diagnostic_admission(self):
        findings = detect_diagnostic_only_admission(self._chunks())
        self.assertTrue(findings)


class TestNotebookEndToEnd(unittest.TestCase):
    def test_apply_fixes_bency_result(self):
        nb = build_case_notebook(
            case_text=BENCY_CORPUS,
            expected_patient_name="Mrs. Bency Biju",
            current_claim="2026077000347",
            current_policy="H1677879",
            admission_date="17/07/2026",
        )
        result = {
            "patient_details": {"name": "Mrs. Bency Bliju", "age": "40", "sex": "Female"},
            "insurance_details": {
                "insurance_company": "SBI",
                "policy_number": "H1677879",
                "claim_incident_number": "2026077000347",
            },
            "claim_details": {
                "hospital": "Daya General Hospital",
                "diagnosis": "RIGHT HEMISPHERIC TRANSIENT ISCHEMIC ATTACK",
                "total_hospital_bill": "Rs. 50,000",
                "date_of_admission": "17/07/2026",
            },
            "observations": [
                {
                    "question": "Identity / record tampering?",
                    "answer": "Supported",
                    "analysis": "hes across document types clinicaLchart",
                },
                {
                    "question": "what are the gaps?",
                    "answer": "Documents lack specific patient data such as age, sex, hospital",
                    "analysis": "missing crucial patient and hospital information",
                }
            ],
            "claim_recommended": "Yes",
            "compliance_verdict": "Partially Compliant",
            "auditor_conclusion": "Approve basic expenses.",
            "fraud_abuse": {"findings": []},
            "financial_review": {"total_hospital_bill": "Rs. 50,000"},
            "treatment_billing_audit": {"charges_appropriate": "YES"},
        }
        out = apply_notebook_to_result(result, nb)
        self.assertEqual(out["insurance_details"]["claim_incident_number"], "2026071700347")
        self.assertEqual(out["insurance_details"]["policy_number"], "H1677679")
        self.assertIn("IFFCO", out["insurance_details"]["insurance_company"].upper())
        self.assertEqual(out.get("claim_recommended"), "No")
        self.assertEqual(
            (out.get("treatment_billing_audit") or {}).get("charges_appropriate"),
            "NO",
        )
        bill = str((out.get("financial_review") or {}).get("total_hospital_bill") or "")
        self.assertTrue("80,800" in bill or "80800" in bill.replace(",", ""))
        # No OCR-garbage Q&As / no FWA duplicated into observations
        for o in out.get("observations") or []:
            blob = str(o.get("question") or "") + str(o.get("analysis") or "")
            self.assertNotIn("hes across", blob.lower())
            self.assertNotIn("identity / record tampering", blob.lower())
        # No Case180 bill numbers on a Bency corpus
        fwa_blob = " ".join(
            str(f.get("evidence") or "") for f in out.get("fwa_investigation") or []
        )
        self.assertNotIn("A021042", fwa_blob)
        # BENCY BIJU N / BUU must not be treated as foreign identity
        inds = " ".join(str(f.get("indicator") or "") for f in out.get("fwa_investigation") or [])
        self.assertNotIn("Patient name mismatch across documents", inds)


if __name__ == "__main__":
    unittest.main()
