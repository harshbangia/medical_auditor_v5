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
Nibedita Health Care
=== Source document: INDOOR CASE PAPER.pdf ===
WARD / BED NO: ICU-3
Inj Doxycycline 100 IV BDPC
Unstable Angina
Trop-T positive
chest discomfort x 3d
fever 3-4 days
Gangapada Super Speciality Hospital Pvt. Ltd.
=== Source document: CT SCAN.pdf ===
CT SCAN OF THORAX
Date: 19/06/2026
HRCT scan of thorax done.
Multifocal consolidate and tiny air space nodules in both lungs — likely infective.
BED NO: ICU-3
=== Source document: ECHO.pdf ===
DOCUMENT TYPE: Echocardiography report
=== Source document: ECG.pdf ===
DOCUMENT TYPE: ECG Report
Sinus tachycardia
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

    def test_extracts_symptom_durations(self):
        evidence = detect_case_evidence(CASE165_SNIPPET)
        durations = evidence.get("symptom_durations") or []
        self.assertIn("3 days (chest discomfort)", durations)
        self.assertIn("3-4 days (fever)", durations)

    def test_dedupes_checklist_and_challenges(self):
        result = {
            "clinical_checklist": [
                {"area": "CT Scan Report", "available": "YES", "remarks": ""},
                {"area": "CT Scan Report", "available": "YES", "remarks": "dup"},
            ],
            "challenge_points": [
                "Duplicate challenge about documentation gaps.",
                "Duplicate challenge about documentation gaps.",
            ],
            "clinical_findings": [],
            "claim_details": {},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        areas = [i["area"] for i in fixed["clinical_checklist"] if isinstance(i, dict)]
        self.assertEqual(areas.count("CT Scan Report"), 1)
        self.assertEqual(len(fixed["challenge_points"]), 1)

    def test_no_preauth_crosscheck_when_form_missing(self):
        result = {
            "clinical_findings": [],
            "clinical_checklist": [],
            "observations": [
                {
                    "question": "Admission type",
                    "analysis": "Cross-checked with pre-auth: YES",
                    "answer": "Supported",
                }
            ],
            "claim_details": {},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        self.assertIn(
            "not present",
            fixed["observations"][0]["analysis"].lower(),
        )
        self.assertFalse(detect_case_evidence(CASE165_SNIPPET)["has_preauth_form"])

    def test_seeds_labs_symptoms_and_culture(self):
        result = {
            "clinical_findings": [
                {"parameter": "Symptom duration at presentation", "value": "3 days (chest discomfort)"},
            ],
            "clinical_checklist": [],
            "claim_details": {},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        params = {f["parameter"]: f for f in fixed["clinical_findings"] if isinstance(f, dict)}
        self.assertIn("Creatinine (Serum)", params)
        self.assertEqual(params["Creatinine (Serum)"]["value"], "1.9 mg/dl")
        self.assertIn("C-Reactive Protein (CRP)", params)
        self.assertIn("fever", params["Symptom duration at presentation"]["value"].lower())
        self.assertIn("treatment sheet", params["Symptom duration at presentation"]["source"].lower())
        self.assertIn("Culture sensitivity", params)

    def test_downgrades_copd_and_reconciles_verdict(self):
        result = {
            "clinical_findings": [],
            "clinical_checklist": [],
            "compliance_verdict": "Non-Compliant",
            "guideline_deviations": [
                {
                    "issue": "Lack of confirmed COPD diagnosis as per guidelines.",
                    "case_evidence": "Spirometry results not documented.",
                    "severity": "High",
                },
            ],
            "challenge_points": ["Justify admission without spirometry confirming COPD diagnosis."],
            "documentation_gaps": ["Spirometry results missing for COPD diagnosis confirmation."],
            "claim_details": {},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        self.assertEqual(fixed["guideline_deviations"][0]["severity"], "Low")
        self.assertEqual(fixed["compliance_verdict"], "Partially Compliant")
        self.assertFalse(fixed["challenge_points"])
        self.assertFalse(fixed["documentation_gaps"])

    def test_acs_observation_acknowledges_documented_workup(self):
        result = {
            "clinical_findings": [],
            "clinical_checklist": [],
            "observations": [
                {
                    "question": "Is there adequate documentation for acute coronary syndrome management?",
                    "analysis": "TIMI risk score and comprehensive ACS management not documented.",
                    "answer": "Insufficient Evidence",
                }
            ],
            "claim_details": {},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, CASE165_SNIPPET)
        self.assertEqual(fixed["observations"][0]["answer"], "Partially Supported")
        self.assertIn("ECG", fixed["observations"][0]["analysis"])

    def test_policy_wording_mri_not_counted_as_report(self):
        case = """
=== Source document: Family Health Protector Policy Wording.pdf ===
MRI and magnetic resonance imaging are covered under this policy wording.
=== Source document: clinical_note.pdf ===
Patient admitted with chest pain. CT scan report attached.
"""
        evidence = detect_case_evidence(case)
        self.assertFalse(evidence["has_mri_report"])

    def test_strips_hallucinated_mri_from_imaging_findings(self):
        case = """
=== Source document: policy_wording.pdf ===
MRI coverage terms in policy wording document.
=== Source document: notes.pdf ===
Clinical notes only. Trigeminal neuralgia evaluation.
"""
        result = {
            "clinical_checklist": [],
            "imaging_findings": [
                {"type": "MRI Brain", "finding": "Normal", "clinical_correlation": "", "consistency_with_diagnosis": ""},
            ],
            "guideline_deviations": [
                {"issue": "Missing MRI documentation", "case_evidence": "No MRI", "severity": "High"},
            ],
            "claim_details": {"diagnosis": "Trigeminal neuralgia"},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, case)
        self.assertFalse(any("mri" in (i.get("type") or "").lower() for i in fixed["imaging_findings"]))
        mri_rows = [
            i for i in fixed["clinical_checklist"]
            if isinstance(i, dict) and "mri" in (i.get("area") or "").lower()
        ]
        self.assertTrue(mri_rows)
        self.assertEqual(mri_rows[0]["available"], "NO")

    def test_no_mri_checklist_for_cardiology(self):
        case = """
=== Source document: notes.pdf ===
Unstable angina, ECG and troponin done. CABG planned.
"""
        result = {
            "clinical_checklist": [
                {"area": "MRI Report", "available": "NO", "remarks": "missing"},
            ],
            "imaging_findings": [],
            "guideline_deviations": [],
            "claim_details": {"diagnosis": "Unstable angina / CAD"},
            "treatment_billing_audit": {},
        }
        fixed = apply_case_evidence_corrections(result, case)
        mri_rows = [
            i for i in fixed["clinical_checklist"]
            if isinstance(i, dict) and "mri" in (i.get("area") or "").lower()
        ]
        self.assertFalse(mri_rows)

    def test_filters_policy_wording_from_clinical_text(self):
        from backend.utils.case_evidence_detector import clinical_case_text

        case = """
=== Source document: Family Health Protector Policy Wording.pdf ===
Policy wording: unless arising out of hospitalization for cardiac care.
MRI and CT coverage definitions.
=== Source document: indoor.pdf ===
Acute L3 compression fracture after fall. Unable to walk. IV analgesics.
Gokuldas Hospital Pvt. Ltd. Date of admission: 18/07/2026
"""
        scoped = clinical_case_text(case)
        self.assertNotIn("Policy wording", scoped)
        self.assertIn("compression fracture", scoped)

    def test_acute_trauma_supports_admission_and_prunes_cardiac_ct(self):
        case = """
=== Source document: indoor.pdf ===
History of fall. Acute mild compression fracture L3. Unable to walk.
Medical management with IV fluids and analgesics. No surgery.
Date of admission: 18/07/2026 Emergency
Gokuldas Hospital Pvt. Ltd.
"""
        result = {
            "clinical_checklist": [
                {"area": "CT Scan Report", "available": "YES", "remarks": ""},
                {"area": "Cardiac Assessment", "available": "YES", "remarks": ""},
                {"area": "Medication Trials", "available": "NO", "remarks": "missing"},
                {"area": "MRI Report", "available": "YES", "remarks": "spine MRI"},
            ],
            "guideline_deviations": [
                {
                    "issue": "Failed conservative care before admission",
                    "case_evidence": "No medication trials documented",
                    "severity": "High",
                },
            ],
            "challenge_points": [
                "Why was emergency admission preferred over OPD management?",
            ],
            "observations": [
                {
                    "question": "Is emergency admission justified?",
                    "analysis": "Emergency admission not justified without failed conservative care.",
                    "answer": "Not Supported",
                },
            ],
            "claim_details": {
                "diagnosis": "Acute mild compression fracture L3",
                "nature_of_admission": "Unknown",
                "procedure_or_surgery": "",
            },
            "treatment_billing_audit": {},
            "compliance_verdict": "Partially Compliant",
        }
        fixed = apply_case_evidence_corrections(result, case)
        areas = {
            (i.get("area") or "").lower()
            for i in fixed["clinical_checklist"]
            if isinstance(i, dict)
        }
        self.assertNotIn("ct scan report", areas)
        self.assertNotIn("cardiac assessment", areas)
        self.assertNotIn("medication trials", areas)
        self.assertEqual(fixed["claim_details"]["nature_of_admission"], "Emergency")
        self.assertEqual(fixed["claim_details"]["procedure_or_surgery"], "Medical management")
        self.assertFalse(
            any("emergency admission" in str(c).lower() for c in fixed["challenge_points"])
        )
        self.assertEqual(fixed["observations"][0]["answer"], "Supported")
        self.assertEqual(
            fixed["guideline_deviations"][0]["severity"],
            "Low",
        )


if __name__ == "__main__":
    unittest.main()
