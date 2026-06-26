"""Tests for insurance and claim detail extractors."""

import unittest

from backend.utils.claim_details_extractor import (
    enrich_claim_facts,
    extract_claim_details_from_text,
    merge_claim_details_into_result,
)
from backend.utils.insurance_extractor import (
    enrich_insurance_facts,
    extract_insurance_from_text,
    merge_insurance_into_result,
)


QUERY_LETTER = """
QUERY LETTER    Date: 24 Jun 2026  Claim Incident : 2026062400280
Member Code : H1486808-3-1 Kokilaben Dhirubai Ambani Hospital & Medical Research Institute
Policy No : H1486808
Proposed Date of Hospitalization 29 Jun 2026
Patient Name DIVYANSH MISHRA
"""

POLICY_SCHEDULE_VISION_OCR = """
Period Of Insurance Start date From: 04/01/2026 End date Till Midnight on: 03/01/2027 11:59:59
Policy No.: H1486808
IFFCO-TOKIO GENERAL INSURANCE CO. LTD
"""
POLICY_SCHEDULE = """
Policy schedule cum Tax Invoice
Policy No.: H1486808
Period Of Insurance
Start date: From 04/01/2026
End date: Till Midnight on 03/01/2027 11:59:59
IFFCO-TOKIO GENERAL INSURANCE CO
"""

PRE_AUTH = """
REQUEST FOR CASHLESS HOSPITALIZATION
Date of first consultation: 02/04/2025
Proposed diagnosis: Left shoulder bankart lesion
"""


class InsuranceExtractorTests(unittest.TestCase):
    def test_rejects_policy_document_false_positive(self):
        bad = extract_insurance_from_text("Policy document uploaded for review")
        self.assertEqual(bad["policy_number"], "")

    def test_extracts_policy_from_query_letter(self):
        facts = extract_insurance_from_text(QUERY_LETTER, source="Querry Letter.pdf")
        self.assertEqual(facts["policy_number"], "H1486808")
        self.assertEqual(facts["claim_incident_number"], "2026062400280")

    def test_extracts_policy_period_from_schedule(self):
        facts = extract_insurance_from_text(POLICY_SCHEDULE, source="Policy document.pdf")
        self.assertEqual(facts["policy_number"], "H1486808")
        self.assertEqual(facts["policy_period"], "04/01/2026 to 03/01/2027")

    def test_extracts_policy_period_from_vision_ocr_format(self):
        facts = extract_insurance_from_text(POLICY_SCHEDULE_VISION_OCR, source="Policy document.pdf")
        self.assertEqual(facts["policy_period"], "04/01/2026 to 03/01/2027")

    def test_merge_overwrites_bad_llm_policy_number(self):
        result = {"insurance_details": {"policy_number": "schedule", "policy_period": ""}}
        facts = extract_insurance_from_text(QUERY_LETTER)
        facts["policy_period"] = "04/01/2026 to 03/01/2027"
        merge_insurance_into_result(result, facts)
        self.assertEqual(result["insurance_details"]["policy_number"], "H1486808")
        self.assertEqual(result["insurance_details"]["policy_period"], "04/01/2026 to 03/01/2027")

    def test_enrich_prefers_query_letter_policy(self):
        combined = POLICY_SCHEDULE + "\n" + QUERY_LETTER
        facts = enrich_insurance_facts(combined)
        self.assertEqual(facts["policy_number"], "H1486808")
        self.assertIn("04/01/2026", facts["policy_period"])


class ClaimDetailsExtractorTests(unittest.TestCase):
    def test_extracts_admission_from_query_letter(self):
        facts = extract_claim_details_from_text(QUERY_LETTER)
        self.assertEqual(facts["date_of_admission"], "29 Jun 2026")
        self.assertIn("Kokilaben", facts["hospital"])

    def test_extracts_consultation_from_pre_auth(self):
        facts = extract_claim_details_from_text(PRE_AUTH)
        self.assertEqual(facts["consultation_date"], "02/04/2025")

    def test_merge_overwrites_blank_or_llm_admission(self):
        result = {
            "claim_details": {
                "consultation_date": "21/01/2022",
                "date_of_admission": "",
                "nature_of_admission": "Unknown",
            },
            "treatment_billing_audit": {},
        }
        facts = enrich_claim_facts(QUERY_LETTER + "\n=== Page 1 — vision transcription (Pre Auth.pdf) ===\n" + PRE_AUTH)
        merge_claim_details_into_result(result, facts)
        self.assertEqual(result["claim_details"]["date_of_admission"], "29 Jun 2026")
        self.assertEqual(result["claim_details"]["consultation_date"], "02/04/2025")
        self.assertEqual(result["claim_details"]["nature_of_admission"], "Planned / Elective")

    def test_clinical_consult_date_and_nature(self):
        clinical = "DOCUMENT TYPE: Handwritten consultation note BODY: Date: 4/6/2026 Name: Mr. Divyansh Mishra"
        facts = extract_claim_details_from_text(clinical, source="Clinical Documents.pdf")
        self.assertEqual(facts["consultation_date"], "4/6/2026")
        facts2 = extract_claim_details_from_text(QUERY_LETTER, source="Querry Letter.pdf")
        self.assertEqual(facts2["nature_of_admission"], "Planned / Elective")

    def test_prefers_clinical_consult_over_stale_preauth(self):
        case = QUERY_LETTER + """
=== Page 1 — vision transcription (Clinical Documents.pdf) ===
DOCUMENT TYPE: Handwritten consultation note
BODY: Date: 4/6/2026 Name: Mr. Divyansh Mishra
=== Page 1 — vision transcription (Pre Auth.pdf) ===
Date of first consultation: 20/11/2022
Date of admission: 20/11/2022
"""
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["consultation_date"], "4/6/2026")
        self.assertEqual(facts["date_of_admission"], "29 Jun 2026")
        self.assertEqual(facts["nature_of_admission"], "Planned / Elective")


if __name__ == "__main__":
    unittest.main()
