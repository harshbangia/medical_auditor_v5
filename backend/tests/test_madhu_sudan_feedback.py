"""Regression: Madhu Sudan pancreatitis — software vs manual Glowix report."""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.ai.audit_result_enricher import seed_deficiency_observations
from backend.utils.glowix_proforma_pdf import generate_glowix_expert_opinion_pdf


class TestPancreatitisNoIchLeakage(unittest.TestCase):
    def test_which_and_icu_do_not_trigger_ich_seeds(self):
        """Prior bug: pattern `ich\\b` matched the word 'which' → ICH Q&As on pancreatitis."""
        result = {
            "claim_details": {
                "diagnosis": "Acute Pancreatitis with peripancreatic necrotic collection (WON)",
                "hospital": "DHANVANTRI HOSPITAL",
            },
            "observations": [
                {
                    "question": "Was a CT scan performed as per severity management guidelines?",
                    "answer": "Partially Supported",
                    "analysis": "CT present; timing unclear.",
                }
            ],
            "auditor_conclusion": (
                "Based on available documents, this is an emergency admission for large "
                "intraparenchymal hemorrhage with ICU management. Claim recommended."
            ),
            "claim_recommended": "Yes",
        }
        case = (
            "Which hospital managed acute pancreatitis in ICU with Merotec (meropenem)? "
            "Patient Madhu Sudan. Librium and Petril also billed. Discharge summary available."
        )
        seed_deficiency_observations(result, case)
        qs = " ".join(
            str(o.get("question") or "") + " " + str(o.get("analysis") or "")
            for o in result["observations"]
        ).lower()
        self.assertNotIn("intraparenchymal", qs)
        self.assertNotIn("neurocritical", qs)
        self.assertNotIn("extended duration of hospitalization", qs)
        conclusion = str(result.get("auditor_conclusion") or result.get("inference") or "")
        self.assertNotRegex(conclusion, r"(?i)intraparenchymal|neurocritical")

    def test_true_ich_still_seeds(self):
        result = {
            "claim_details": {"diagnosis": "Large Intraparenchymal hemorrhage Brain"},
            "observations": [],
            "auditor_conclusion": "Recommend denying claim due to patient identity issues.",
        }
        case = "Unconscious patient ICU large intraparenchymal hemorrhage. Inj Meropenem."
        seed_deficiency_observations(result, case)
        qs = " ".join(o["question"] for o in result["observations"]).lower()
        self.assertIn("extended duration", qs)
        self.assertEqual(result["claim_recommended"], "Yes")


class TestAuditorSignature(unittest.TestCase):
    def test_default_auditor_is_saharan(self):
        path = tempfile.mktemp(suffix=".pdf")
        try:
            out = generate_glowix_expert_opinion_pdf(
                {
                    "patient_details": {"name": "Mr. Madhu Sudan", "age": "38", "sex": "Male"},
                    "claim_details": {"diagnosis": "Acute Pancreatitis"},
                    "inference": "Alcohol-related pancreatitis suspected; review under exclusions.",
                },
                path,
            )
            self.assertTrue(os.path.isfile(out))
            import fitz

            text = "".join(page.get_text() for page in fitz.open(out))
            self.assertIn("D.V. Saharan", text)
            self.assertIn("MD (AIIMS)", text)
            self.assertNotIn("Virender Nagpal", text)
        finally:
            if os.path.isfile(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
