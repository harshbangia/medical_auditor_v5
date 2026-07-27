"""Tests for Architecture V2 agent foundation."""

import unittest

from backend.agents.planner import build_audit_plan
from backend.agents.case_graph import build_medical_case_record
from backend.agents.timeline_agent import build_clinical_timeline
from backend.agents.evidence_verifier import verify_audit_result
from backend.agents.orchestrator import apply_agent_postprocess


class PlannerTests(unittest.TestCase):
    def test_plan_has_ordered_stages(self):
        plan = build_audit_plan(document_count=3, has_handwriting=True, guidelines=["Hep.pdf"])
        ids = [s.step_id for s in plan.steps]
        self.assertIn("map", ids)
        self.assertIn("verify", ids)
        self.assertLess(ids.index("map"), ids.index("audit"))
        self.assertLess(ids.index("audit"), ids.index("verify"))
        self.assertTrue(any("Handwriting" in n for n in plan.notes))


class TimelineAndMCRTests(unittest.TestCase):
    def test_timeline_from_ledger(self):
        ledger = {
            "merged": {
                "patient_name": "Pawan Kumar",
                "diagnosis": "Hepatitis",
                "admission_date": "01/07/2026",
                "discharge_date": "05/07/2026",
                "bill_amount": "Rs. 52,881",
                "medications": ["Ursodeoxycholic acid"],
                "key_labs": ["SGPT 120"],
            },
            "documents": [{"source_file": "discharge.pdf"}],
            "conflicts": [],
        }
        events = build_clinical_timeline(ledger, {"date_of_admission": "01/07/2026"})
        labels = " ".join(e.event for e in events)
        self.assertIn("Admission", labels)
        self.assertIn("Discharge", labels)
        self.assertIn("bill", labels.lower())

        mcr = build_medical_case_record(ledger)
        self.assertEqual(mcr.patient_name, "Pawan Kumar")
        self.assertTrue(mcr.identity_complete())
        self.assertIn("Hepatitis", mcr.to_prompt_block())


class VerifierTests(unittest.TestCase):
    def test_drops_empty_deviation_and_flags_observation(self):
        case = "=== Source document: Accord discharge.pdf ===\nHepatitis treated"
        result = {
            "observations": [
                {"question": "Is endoscopy done?", "answer": "Not Supported", "analysis": "No mention"},
                {
                    "question": "Diagnosis?",
                    "answer": "Supported",
                    "analysis": "In Accord discharge.pdf hepatitis is documented",
                },
            ],
            "guideline_deviations": [
                {"issue": "Empty", "case_evidence": ""},
                {
                    "issue": "Missing Fibroscan",
                    "case_evidence": "Accord discharge.pdf does not show Fibroscan",
                },
            ],
            "clinical_findings": [
                {"parameter": "Dx", "value": "", "source": ""},
                {"parameter": "SGPT", "value": "120", "source": "Accord discharge.pdf"},
            ],
            "patient_details": {"name": "Pawan Kumar"},
            "claim_details": {"diagnosis": "Hepatitis"},
        }
        out = verify_audit_result(
            result,
            case_text=case,
            source_summaries=[{"filename": "Accord discharge.pdf"}],
        )
        self.assertEqual(len(out["guideline_deviations"]), 1)
        self.assertEqual(len(out["clinical_findings"]), 1)
        self.assertFalse(out["observations"][0]["evidence_supported"])
        self.assertTrue(out["observations"][1]["evidence_supported"])
        self.assertIn("verification", out)


class OrchestratorTests(unittest.TestCase):
    def test_postprocess_attaches_plan_and_mcr(self):
        ledger = {
            "merged": {
                "patient_name": "Pawan Kumar",
                "diagnosis": "Hepatitis",
                "admission_date": "01/07/2026",
                "discharge_date": "05/07/2026",
            },
            "documents": [{"source_file": "d.pdf"}],
            "conflicts": [],
        }
        result = {
            "patient_details": {},
            "claim_details": {},
            "observations": [],
            "clinical_findings": [],
            "timeline": [],
        }
        out = apply_agent_postprocess(
            result,
            case_facts_ledger=ledger,
            claim_facts={"date_of_admission": "01/07/2026"},
            guidelines=["Viral Hepatitis.pdf"],
        )
        self.assertEqual(out["patient_details"]["name"], "Pawan Kumar")
        self.assertEqual(out["claim_details"]["diagnosis"], "Hepatitis")
        self.assertIn("audit_plan", out)
        self.assertIn("medical_case_record", out)
        self.assertTrue(out["timeline"])


if __name__ == "__main__":
    unittest.main()
