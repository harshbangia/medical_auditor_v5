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
Claim Number: 2026071700347
Sub Claim Number: 2026071700347.R1
Policy Number: H1677679
Name of The Insured: BENCY BIJU
Claimed Amount: 80800
Claim Type: Reimbursement
Hospital Name: Daya General Hospital Ltd
FWA Alerts Identity Check: clinical charts contain SAVITHA A G Patient ID 5309
demographic mismatches and record mixing. Bill Amount Verification failed aggregate-sum check.

=== Source document: indoor_notes.pdf ===
=== Page 1 — vision transcription (indoor_notes.pdf) ===
Patient Name: SAVITHA A G
Patient ID: 5309
Also chart header BENCY BUU
Pharmacy extract issued to DAYA THE

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
"""


class TestClaimRepair(unittest.TestCase):
    def test_bency_claim_ocr_prefers_2026(self):
        bad = "2020877700347"
        good = "2026071700347"
        picked = repair_claim_ocr_candidates([bad, good])
        self.assertEqual(picked.split(".")[0], "2026071700347")

    def test_assessor_wins_over_corrupt(self):
        ids = validate_ids_from_corpus(
            "Claim Incident No: 2020877700347 Policy H1767679",
            assessor={"claim_number": "2026071700347", "policy_number": "H1677679"},
            current_claim="2020877700347",
            current_policy="H1767679",
        )
        self.assertEqual(ids["claim_incident_number"].split(".")[0], "2026071700347")


class TestAssessorParse(unittest.TestCase):
    def test_parse_claim_and_fwa(self):
        parsed = parse_assessor_text(BENCY_CORPUS, "Health Claim Assessor Report.pdf")
        self.assertTrue(parsed["is_assessor"])
        self.assertIn("2026071700347", parsed["claim_number"])
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
        self.assertTrue("savitha" in blob or "buu" in blob)

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
            current_claim="2020877700347",
            current_policy="H1767679",
        )
        result = {
            "patient_details": {"name": "Mrs. Bency Bliju", "age": "40", "sex": "Female"},
            "insurance_details": {
                "insurance_company": "SBI",
                "policy_number": "H1767679",
                "claim_incident_number": "2020877700347",
            },
            "claim_details": {
                "hospital": "Daya General Hospital",
                "diagnosis": "RIGHT HEMISPHERIC TRANSIENT ISCHEMIC ATTACK",
                "total_hospital_bill": "Rs. 50,000",
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
        self.assertIn("IFFCO", out["insurance_details"]["insurance_company"].upper())
        self.assertEqual(out.get("claim_recommended"), "No")
        self.assertEqual(
            (out.get("treatment_billing_audit") or {}).get("charges_appropriate"),
            "NO",
        )
        self.assertTrue(out.get("fwa_investigation"))
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
        # BENCY BIJU N must not be treated as foreign identity
        inds = " ".join(str(f.get("indicator") or "") for f in out.get("fwa_investigation") or [])
        self.assertNotIn("Patient name mismatch across documents", inds)


if __name__ == "__main__":
    unittest.main()
