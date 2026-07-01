"""Tests for case evidence detection and audit corrections."""

import unittest

from backend.utils.case_evidence_detector import (
    apply_case_evidence_corrections,
    detect_case_evidence,
    extract_lab_values,
)

CASE165_SNIPPET = """
=== Source document: CURRENT INVESTIGATION REPORT.pdf ===
PATIENT NAME: MANOS KUMAR MONDAL
RECEIVING DATE: 24/06/2026
Creatinine (Serum)  1.9            mg/dl
C-Reactive Protein (CRP)   289.7           mg/dl
Culture shows growth of Klebsiella
=== Source document: INDOOR CASE PAPER.pdf ===
Inj Doxycycline 100 IV BDPC
Unstable Angina
Trop-T positive
=== Source document: CT SCAN.pdf ===
CT SCAN OF THORAX
HRCT scan of thorax done.
Multifocal consolidate and tiny air space nodules in both lungs — likely infective.
=== Source document: ECHO.pdf ===
DOCUMENT TYPE: Echocardiography report
=== Source document: ECG.pdf ===
DOCUMENT TYPE: ECG Report
Sinus tachycardia
BED NO: ICU-3
"""


class CaseEvidenceDetectorTests(unittest.TestCase):
    def test_extracts_plausible_creatinine(self):
        labs = extract_lab_values(CASE165_SNIPPET)
        self.assertEqual(labs.get("creatinine"), "1.9 mg/dl")

    def test_detects_antibiotics_cardiac_ct(self):
        evidence = detect_case_evidence(CASE165_SNIPPET)
        self.assertTrue(evidence["has_antibiotics"])
        self.assertTrue(evidence["has_cardiac_workup"])
        self.assertTrue(evidence["has_ct_report"])
        self.assertFalse(evidence["has_mri_report"])
        self.assertFalse(evidence["has_copd_diagnosis"])

    def test_corrects_false_antibiotic_and_cardiac_gaps(self):
        result = {
            "clinical_findings": [
                {"parameter": "Creatinine (Serum)", "value": "19 mg/dl", "comment": "Elevated"},
            ],
            "clinical_checklist": [
                {"area": "Antibiotic Therapy", "available": "NO", "remarks": ""},
                {"area": "Cardiac Assessment", "available": "NO", "remarks": ""},
                {"area": "MRI Report", "available": "YES", "remarks": ""},
            ],
            "guideline_deviations": [
                {
                    "issue": "Absence of antibiotic therapy",
                    "case_evidence": "No antibiotic treatment documented.",
                    "severity": "High",
                },
                {
                    "issue": "Lack of cardiac evaluation",
                    "case_evidence": "No cardiac investigations documented.",
                    "severity": "Medium",
                },
            ],
            "challenge_points": [
                "Why was no antibiotic treatment initiated?",
                "Why were no cardiac evaluations conducted?",
            ],
            "claim_details": {"nature_of_admission": "Unknown"},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        self.assertEqual(fixed["clinical_findings"][0]["value"], "1.9 mg/dl")
        checklist = {i["area"]: i for i in fixed["clinical_checklist"] if isinstance(i, dict)}
        self.assertEqual(checklist.get("Antibiotic Therapy", {}).get("available"), "YES")
        self.assertEqual(checklist.get("Cardiac Assessment", {}).get("available"), "YES")
        self.assertEqual(checklist.get("CT Scan Report", {}).get("available"), "YES")
        self.assertEqual(fixed["claim_details"]["nature_of_admission"], "Emergency")
        self.assertTrue(len(fixed["challenge_points"]) < 2)


if __name__ == "__main__":
    unittest.main()
