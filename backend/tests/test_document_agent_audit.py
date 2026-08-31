"""Tests for INR money helpers and document-agent financial normalize."""

from __future__ import annotations

import tempfile
import os
import unittest

from backend.services.document_agent_audit import _normalize_result, _parse_json, audit_pipeline_mode
from backend.utils.inr_money import parse_inr, recompute_financial_review, sum_disallowances
from backend.utils.glowix_proforma_pdf import generate_glowix_expert_opinion_pdf


class TestInrMoney(unittest.TestCase):
    def test_parse_indian_and_western(self):
        self.assertEqual(parse_inr("Rs. 4,07,597"), 407597.0)
        self.assertEqual(parse_inr("Rs. 15,000"), 15000.0)
        self.assertEqual(parse_inr("15000"), 15000.0)
        self.assertEqual(parse_inr(12000), 12000.0)

    def test_recompute_fixes_junk_header(self):
        data = {
            "claim_details": {"total_hospital_bill": "Rs. 4,07,597"},
            "financial_review": {
                "total_hospital_bill": "Rs. 4,07,597",
                "non_payable_amount": "1",
                "net_claimable_amount": "0.00",
                "recommended_approval_amount": "0.00",
                "patient_liability": "1.09",
            },
            "claim_savings": {"admissible_amount": "0.00", "total_claim_amount": "407597"},
            "billing_disallowances": [
                {"title": "Laparoscopy", "amount": "Rs. 15,000"},
                {"title": "Physio upcoding", "amount": "Rs. 12,000"},
                {"title": "Missing labs", "amount": "Rs. 12,300"},
                {"title": "Registration", "amount": "Rs. 500"},
                {"title": "Syringe pump", "amount": "Rs. 1,000"},
                {"title": "Consumables", "amount": "Rs. 10,000"},
            ],
        }
        out = recompute_financial_review(data)
        self.assertEqual(sum_disallowances(out), 50800.0)
        self.assertEqual(out["financial_review"]["non_payable_amount"], "Rs. 50,800")
        self.assertEqual(out["financial_review"]["net_claimable_amount"], "Rs. 356,797")
        self.assertEqual(out["financial_review"]["recommended_approval_amount"], "Rs. 356,797")
        self.assertEqual(out["financial_review"]["patient_liability"], "Rs. 50,800")
        self.assertEqual(out["claim_savings"]["admissible_amount"], "Rs. 356,797")

    def test_pdf_uses_recomputed_finance(self):
        data = {
            "report_date": "31-08-2026",
            "patient_details": {"name": "Mr. Nandkishor Gupta", "age": "53", "sex": "Male"},
            "insurance_details": {
                "insurance_company": "IFFCO",
                "policy_number": "H1",
                "claim_incident_number": "C1",
            },
            "claim_details": {
                "hospital": "Stellars",
                "diagnosis": "IO",
                "total_hospital_bill": "407597",
            },
            "financial_review": {
                "total_hospital_bill": "407597",
                "non_payable_amount": "1",
                "net_claimable_amount": "0",
                "recommended_approval_amount": "0",
            },
            "claim_savings": {"admissible_amount": "0.00"},
            "billing_disallowances": [
                {"title": "Lap", "amount": "15000", "reason": "open OT", "audit_action": "Disallow"},
                {"title": "Physio", "amount": "12000", "reason": "miscoded", "audit_action": "Disallow"},
            ],
            "observations": [
                {
                    "question": "Is surgery justified?",
                    "answer": "Supported",
                    "analysis": (
                        "OT notes confirm adhesiolysis for obstruction with clinical correlation "
                        "and guideline alignment for emergency laparotomy after failed conservative care."
                    ),
                }
            ],
            "compliance_verdict": "Partially Compliant",
            "claim_recommended": "Yes",
            "claim_not_recommended": "No",
            "inference": "Clinically genuine with billing anomalies.",
        }
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            generate_glowix_expert_opinion_pdf(data, path)
            import fitz

            doc = fitz.open(path)
            text = "\n".join(doc.load_page(i).get_text() for i in range(len(doc)))
            compact = text.replace(" ", "").replace("\n", "")
            self.assertIn("27,000", compact)
            self.assertIn("380,597", compact)
            self.assertNotIn("Non-PayableAmount:Rs.1", compact)
            self.assertNotIn("NetClaimableAmount:0.00", compact)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


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
        self.assertEqual(out["report_format"], "expert_opinion_pdf")
        self.assertNotIn("report_html", out)

    def test_normalize_recomputes_finance_and_dedupes_stub_qs(self):
        data = {
            "claim_details": {"total_hospital_bill": "407597"},
            "financial_review": {
                "total_hospital_bill": "407597",
                "non_payable_amount": "1",
                "net_claimable_amount": "0.00",
                "recommended_approval_amount": "0.00",
                "patient_liability": "1.09",
            },
            "claim_savings": {"admissible_amount": "0.00"},
            "billing_disallowances": [
                {
                    "title": "Physiotherapist misclassified as Super Specialist",
                    "amount": "12000",
                    "reason": "Dr. Surbhi notes signed as Physiotherapist",
                    "evidence": "Progress note 21/08/2026",
                    "audit_action": "Recommend complete disallowance of Rs. 12,000",
                },
                {
                    "title": "Unrendered laparoscopy charges",
                    "amount": "15000",
                    "reason": "Open laparotomy only",
                    "evidence": "OT notes midline incision",
                    "audit_action": "Recommend complete disallowance of Rs. 15,000",
                },
            ],
            "documentation_gaps": [
                {
                    "title": "Missing GeneXpert / HPE reports",
                    "finding": "Infibeam Labs receipt billed but reports absent",
                    "evidence": "Receipt I-33060 dated 20/08/2026",
                    "audit_action": "Query hospital for HPE, GeneXpert and AFB culture reports",
                }
            ],
            "observations": [
                {
                    "question": "Is laparotomy justified?",
                    "answer": "Supported",
                    "analysis": (
                        "Emergency presentation with CT evidence of subacute obstruction and OT findings of "
                        "adhesive bands forming an abdominal cocoon fully justify exploratory laparotomy."
                    ),
                },
                {
                    "question": "Documentation / forensic gap: Missing GeneXpert / HPE reports?",
                    "answer": "Supported",
                    "analysis": "Reports missing despite billing.",
                },
                {
                    "question": "Is the billed item 'Unrendered laparoscopy charges' admissible / correctly classified?",
                    "answer": "Not Supported",
                    "analysis": "Amount: 15000 Open laparotomy only.",
                },
            ],
        }
        out = _normalize_result(data, [], [])
        self.assertEqual(out["financial_review"]["non_payable_amount"], "Rs. 27,000")
        self.assertEqual(out["financial_review"]["net_claimable_amount"], "Rs. 380,597")
        self.assertEqual(out["financial_review"]["recommended_approval_amount"], "Rs. 380,597")
        qs = [o["question"] for o in out["observations"]]
        self.assertEqual(len(qs), 1)
        self.assertIn("laparotomy justified", qs[0].lower())
        self.assertTrue(all(not q.lower().startswith("documentation / forensic") for q in qs))
        self.assertTrue(all("billed item" not in q.lower() for q in qs))

    def test_pipeline_mode(self):
        self.assertIn(audit_pipeline_mode(), {"legacy", "document_agent"})


if __name__ == "__main__":
    unittest.main()
