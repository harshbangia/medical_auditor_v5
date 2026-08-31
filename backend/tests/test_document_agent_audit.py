"""Tests for Gemini document-agent audit helpers."""

from __future__ import annotations

import unittest

from backend.services.document_agent_audit import (
    _normalize_result,
    _parse_json,
    audit_pipeline_mode,
)


class TestDocumentAgentHelpers(unittest.TestCase):
    def test_parse_json_fences(self):
        raw = '```json\n{"patient_details": {"name": "Bhagyashri", "age": "58"}}\n```'
        data = _parse_json(raw)
        self.assertEqual(data["patient_details"]["name"], "Bhagyashri")

    def test_normalize_fills_ui_fields(self):
        data = {
            "compliance_verdict": "Partially Compliant",
            "claim_recommended": "Yes",
            "patient_details": {"name": "Bhagyashri Vitthal Tatkare", "age": "58", "sex": "Female"},
            "insurance_details": {
                "insurance_company": "IFFCO-Tokio",
                "policy_number": "H1684623",
                "claim_incident_number": "20260708000052",
            },
            "claim_details": {
                "hospital": "Wellness Clinic",
                "diagnosis": "Bronchitis",
                "date_of_admission": "06/07/2026",
                "final_diagnosis": "Bronchitis",
                "total_hospital_bill": "Rs. 79,324",
            },
            "observations": [
                {
                    "question": "Is ICU justified?",
                    "answer": "Insufficient Evidence",
                    "analysis": "Assessor and discharge support bronchitis; vitals incomplete.",
                }
            ],
        }
        out = _normalize_result(data, [("a.pdf", b"%PDF")], ["Bronchitis.pdf"])
        self.assertEqual(out["patient_details"]["name"], "Bhagyashri Vitthal Tatkare")
        self.assertEqual(out["insurance_details"]["claim_incident_number"], "20260708000052")
        self.assertEqual(out["claim_details"]["diagnosis"], "Bronchitis")
        self.assertEqual(out["financial_review"]["total_hospital_bill"], "Rs. 79,324")
        self.assertEqual(out["report_format"], "expert_opinion_pdf")
        self.assertNotIn("report_html", out)
        self.assertEqual(out["claim_recommended"], "Yes")

    def test_normalize_alias_keys(self):
        data = {
            "patient_details": {"patient_name": "Test Patient", "gender": "Male", "age": "40"},
            "insurance_details": {
                "company": "IFFCO",
                "policy_no": "P1",
                "claim_number": "C1",
            },
            "claim_details": {
                "hospital_name": "City Hospital",
                "final_diagnosis": "COPD",
                "doa": "01/01/2026",
                "dod": "05/01/2026",
            },
        }
        out = _normalize_result(data, [], [])
        self.assertEqual(out["patient_details"]["name"], "Test Patient")
        self.assertEqual(out["patient_details"]["sex"], "Male")
        self.assertEqual(out["insurance_details"]["claim_incident_number"], "C1")
        self.assertEqual(out["claim_details"]["diagnosis"], "COPD")
        self.assertEqual(out["claim_details"]["hospital"], "City Hospital")
        self.assertEqual(out["claim_details"]["date_of_admission"], "01/01/2026")

    def test_pipeline_mode(self):
        self.assertIn(audit_pipeline_mode(), {"legacy", "document_agent"})


if __name__ == "__main__":
    unittest.main()
