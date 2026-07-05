"""Tests for guideline alignment, fraud/abuse, savings, PPI, and doctor registration."""

import unittest

from backend.utils.guideline_alignment import (
    GuidelineMismatchError,
    assert_guideline_alignment,
    check_guideline_alignment,
)
from backend.utils.fraud_abuse_detector import detect_fraud_abuse
from backend.utils.claim_savings import build_claim_savings
from backend.ai.audit_result_enricher import (
    enrich_audit_result,
    _remove_ppi_exclusions,
    _ensure_inference_and_summary,
)
from backend.ai.drug_normalizer import build_medication_evidence_section


class GuidelineAlignmentTests(unittest.TestCase):
    def test_blocks_hypoglycemia_guideline_on_cardiac_case(self):
        profile = {"diagnosis": "Unstable angina, CAD, triple vessel disease", "procedures": ["CABG"]}
        result = check_guideline_alignment(
            ["Hypoglycemia Guideline RG.pdf"],
            profile,
            case_text="Chest pain, ECG changes, troponin elevated. Planned CABG.",
        )
        self.assertFalse(result["aligned"])

    def test_allows_cardiac_guideline_on_cardiac_case(self):
        profile = {"diagnosis": "Unstable angina", "procedures": ["CABG"]}
        result = check_guideline_alignment(
            ["Clinical Guidelines Coronary Heart Disease.pdf"],
            profile,
            case_text="Coronary artery disease",
        )
        self.assertTrue(result["aligned"])

    def test_allows_matching_guideline_despite_comorbid_noise(self):
        """Right guideline must pass even if OCR mentions other specialties."""
        profile = {
            "diagnosis": "Hypoglycemia under evaluation with chest pain under evaluation",
            "procedures": [],
        }
        result = check_guideline_alignment(
            ["Hypoglycemia Guideline RG.pdf"],
            profile,
            case_text=(
                "Hypoglycemia under evaluation. Chest pain. Creatinine 1.2. "
                "Fever on day 2. Infection workup. ICU observation."
            ),
        )
        self.assertTrue(result["aligned"])

    def test_allows_when_guideline_topic_in_case_text_only(self):
        profile = {"diagnosis": "Under evaluation", "procedures": []}
        result = check_guideline_alignment(
            ["Hypoglycemia Guideline RG.pdf"],
            profile,
            case_text="Patient admitted with hypoglycemia and low blood sugar.",
        )
        self.assertTrue(result["aligned"])

    def test_allows_unclassified_guideline_filename(self):
        result = check_guideline_alignment(
            ["General Medical Protocol v2.pdf"],
            {"diagnosis": "Unstable angina"},
            case_text="CAD",
        )
        self.assertTrue(result["aligned"])

    def test_assert_raises_on_mismatch(self):
        with self.assertRaises(GuidelineMismatchError):
            assert_guideline_alignment(
                ["Hypoglycemia Guideline RG.pdf"],
                {"diagnosis": "Trigeminal neuralgia"},
                case_text="Facial pain neuralgia MVD planned.",
            )

    def test_blocks_enteric_fever_guideline_on_alcohol_case(self):
        profile = {"diagnosis": "Alcohol dependence with withdrawal", "procedures": []}
        result = check_guideline_alignment(
            ["Enteric Fever Clinical Guidelines.pdf"],
            profile,
            case_text="Patient admitted for alcohol withdrawal and detoxification.",
            claim_diagnosis="Alcohol dependence",
        )
        self.assertFalse(result["aligned"])
        self.assertEqual(result["reason"], "disease_topic_mismatch")

    def test_blocks_enteric_fever_when_only_claim_diagnosis_differs(self):
        result = check_guideline_alignment(
            ["Enteric Fever Guideline.pdf"],
            {"diagnosis": "Under evaluation", "procedures": []},
            case_text="Detox and counselling for alcohol use disorder.",
            claim_diagnosis="Alcohol use disorder",
        )
        self.assertFalse(result["aligned"])


class FraudAbuseTests(unittest.TestCase):
    def test_detects_history_contradiction(self):
        text = (
            "Discharge summary: known case of diabetes mellitus and hypertension on regular medication. "
            "Patient self-declaration: never taken these medicines, first time detected."
        )
        out = detect_fraud_abuse(text, {})
        self.assertEqual(out["risk_level"], "High")
        self.assertTrue(out["findings"])
        self.assertTrue(any("Contradiction" in f["indicator"] for f in out["findings"]))


class ClaimSavingsTests(unittest.TestCase):
    def test_savings_table_and_percentage(self):
        result = {
            "financial_review": {
                "total_hospital_bill": "Rs. 100,000",
                "non_payable_amount": "Rs. 10,000",
                "net_claimable_amount": "Rs. 90,000",
            },
            "treatment_billing_audit": {
                "excluded_items_billed": "Admission charges Rs. 5,000; Documentation charges Rs. 5,000",
            },
        }
        savings = build_claim_savings(result, "")
        self.assertIn("Rs.", savings["amount_saved"])
        self.assertIn("%", savings["savings_percentage"])
        self.assertTrue(savings["line_items"])
        self.assertEqual(result["financial_review"]["savings_percentage"], savings["savings_percentage"])

    def test_ppi_not_in_savings_rows(self):
        result = {
            "financial_review": {"total_hospital_bill": "Rs. 50,000"},
            "treatment_billing_audit": {"excluded_items_billed": "Pantoprazole Rs. 2,000"},
            "claim_savings_line_items": [
                {"item": "Pantoprazole", "billed_amount": "Rs. 2,000", "admissible_amount": "Rs. 0",
                 "amount_saved": "Rs. 2,000", "reason": "non-payable"},
            ],
        }
        savings = build_claim_savings(result, "")
        for row in savings["line_items"]:
            self.assertNotIn("panto", (row.get("item") or "").lower())


class PpiExclusionTests(unittest.TestCase):
    def test_drug_normalizer_does_not_call_ppi_non_payable(self):
        section = build_medication_evidence_section("Tab Pan 40 mg OD")
        self.assertIn("pantoprazole", section.lower())
        self.assertIn("do not list as unadvised", section.lower())
        self.assertNotIn("often a non-payable line item", section.lower())

    def test_remove_ppi_from_excluded_items(self):
        result = {
            "treatment_billing_audit": {
                "excluded_items_billed": "Pantoprazole, Admission charges",
            },
            "challenge_points": [
                "Pantoprazole is unadvised and non-payable",
                "Justify ICU admission",
            ],
            "guideline_deviations": [],
            "observations": [],
            "documentation_gaps": [],
            "financial_review": {},
        }
        _remove_ppi_exclusions(result)
        self.assertNotIn("Pantoprazole", result["treatment_billing_audit"]["excluded_items_billed"])
        self.assertTrue(any("ICU" in c for c in result["challenge_points"]))
        self.assertFalse(any("Pantoprazole" in c for c in result["challenge_points"]))


class InferenceSummaryTests(unittest.TestCase):
    def test_builds_inference_and_bullet_summary(self):
        result = {
            "patient_details": {"name": "Mrs. Test", "age": "35", "sex": "Female"},
            "claim_details": {
                "hospital": "Test Hospital",
                "diagnosis": "Hypoglycemia under evaluation",
                "date_of_admission": "04/12/2025",
                "date_of_discharge": "08/12/2025",
                "nature_of_admission": "Emergency",
            },
            "insurance_details": {"insurance_company": "IFFCO-TOKIO", "policy_number": "H1607192"},
            "compliance_verdict": "Partially Compliant",
            "inference": "The claim presents partial compliance with guidelines.",
            "fraud_abuse": {"risk_level": "Low", "findings": [], "summary": "None"},
            "claim_savings": {
                "total_claim_amount": "Rs. 80,000",
                "amount_saved": "Rs. 5,000",
                "savings_percentage": "6.3%",
            },
            "financial_review": {},
            "documentation_gaps": ["Conflicting admission dates must be reconciled."],
        }
        _ensure_inference_and_summary(result)
        self.assertGreater(len(result["inference"]), 40)
        self.assertNotIn("The claim presents partial compliance with guidelines.", result["inference"])
        self.assertTrue(result["report_summary"])
        self.assertTrue(any("Patient:" in b for b in result["report_summary"]))
        self.assertTrue(any("Recommendation:" in b or "Compliance" in b for b in result["report_summary"]))


class EnrichmentIntegrationTests(unittest.TestCase):
    def test_enrich_adds_new_sections(self):
        result = {
            "patient_details": {"name": "Test Patient"},
            "claim_details": {"diagnosis": "Hypoglycemia under evaluation"},
            "clinical_checklist": [{"area": "MRI Report", "available": "NO", "remarks": "missing"}],
            "treatment_billing_audit": {"excluded_items_billed": "Pantoprazole"},
            "financial_review": {"total_hospital_bill": "Rs. 80,000"},
            "clinical_findings": [],
            "observations": [],
            "guideline_deviations": [],
            "challenge_points": [],
            "documentation_gaps": [],
            "inference": "",
        }
        case = "Patient with hypoglycemia. Tab Pan 40 given."
        fixed = enrich_audit_result(result, case)
        self.assertNotIn("doctor_validation", fixed)
        self.assertIn("fraud_abuse", fixed)
        self.assertIn("claim_savings", fixed)
        self.assertTrue(fixed.get("inference"))
        self.assertTrue(fixed.get("report_summary"))
        mri_rows = [
            i for i in fixed.get("clinical_checklist") or []
            if isinstance(i, dict) and "mri" in (i.get("area") or "").lower()
        ]
        self.assertFalse(mri_rows)
        self.assertNotIn("Pantoprazole", (fixed.get("treatment_billing_audit") or {}).get("excluded_items_billed") or "")


if __name__ == "__main__":
    unittest.main()
