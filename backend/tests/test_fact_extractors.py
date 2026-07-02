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
Date of admission: 30/6
Proposed diagnosis: Left shoulder bankart lesion
"""

PRE_AUTH_GENERIC_FILENAME = """
REQUEST FOR CASHLESS HOSPITALIZATION
Date of first consultation: 02/04/2025
Date of admission: 30/6
"""


class InsuranceExtractorTests(unittest.TestCase):
    def test_rejects_policy_document_false_positive(self):
        bad = extract_insurance_from_text("Policy document uploaded for review")
        self.assertEqual(bad["policy_number"], "")

    def test_extracts_policy_from_query_letter(self):
        facts = extract_insurance_from_text(QUERY_LETTER, source="letter_from_insurer.pdf")
        self.assertEqual(facts["policy_number"], "H1486808")
        self.assertEqual(facts["claim_incident_number"], "2026062400280")

    def test_extracts_policy_period_from_schedule(self):
        facts = extract_insurance_from_text(POLICY_SCHEDULE, source="policy_schedule.pdf")
        self.assertEqual(facts["policy_number"], "H1486808")
        self.assertEqual(facts["policy_period"], "04/01/2026 to 03/01/2027")

    def test_extracts_policy_period_from_vision_ocr_format(self):
        facts = extract_insurance_from_text(POLICY_SCHEDULE_VISION_OCR, source="policy_schedule.pdf")
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
    def test_query_letter_proposed_date_not_actual_admission(self):
        facts = extract_claim_details_from_text(QUERY_LETTER, source="insurer_query.pdf")
        self.assertEqual(facts["date_of_admission"], "")
        self.assertEqual(facts["proposed_hospitalization_date"], "29 Jun 2026")
        self.assertIn("Kokilaben", facts["hospital"])

    def test_extracts_handwritten_partial_admission_from_pre_auth(self):
        text = PRE_AUTH + "\nClaim Incident : 2026062400280"
        facts = extract_claim_details_from_text(text, source="hospital_form_scan.pdf")
        self.assertEqual(facts["consultation_date"], "02/04/2025")
        self.assertEqual(facts["date_of_admission"], "30/06/2026")

    def test_prefers_preauth_over_query_and_flags_discrepancy(self):
        case = (
            f"=== Source document: insurer_query.pdf ===\n{QUERY_LETTER}\n\n"
            f"=== Source document: hospital_form_scan.pdf ===\n{PRE_AUTH}"
        )
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["consultation_date"], "02/04/2025")
        self.assertEqual(facts["date_of_admission"], "30/06/2026")
        self.assertEqual(facts["proposed_hospitalization_date"], "29 Jun 2026")
        self.assertIn("hospital_form_scan.pdf", facts["date_of_admission_source"])
        self.assertTrue(facts["date_discrepancies"])
        self.assertTrue(any(d["field"] == "date_of_admission" for d in facts["date_discrepancies"]))
        self.assertTrue(len(facts["all_document_dates"]) >= 3)

    def test_merge_attaches_sources_and_discrepancies(self):
        result = {
            "claim_details": {
                "consultation_date": "21/01/2022",
                "date_of_admission": "29 Jun 2026",
                "nature_of_admission": "Unknown",
            },
            "treatment_billing_audit": {},
        }
        case = (
            f"=== Source document: insurer_query.pdf ===\n{QUERY_LETTER}\n\n"
            f"=== Source document: hospital_form_scan.pdf ===\n{PRE_AUTH}"
        )
        facts = enrich_claim_facts(case)
        merge_claim_details_into_result(result, facts)
        self.assertEqual(result["claim_details"]["consultation_date"], "02/04/2025")
        self.assertEqual(result["claim_details"]["date_of_admission"], "30/06/2026")
        self.assertEqual(result["claim_details"]["proposed_hospitalization_date"], "29 Jun 2026")
        self.assertIn("consultation_date_source", result["claim_details"])
        self.assertTrue(result.get("date_discrepancies"))

    def test_clinical_consult_date_and_nature(self):
        clinical = "DOCUMENT TYPE: Handwritten consultation note BODY: Date: 4/6/2026 Name: Mr. Divyansh Mishra"
        facts = extract_claim_details_from_text(clinical, source="opd_note.pdf")
        self.assertEqual(facts["consultation_date"], "4/6/2026")
        facts2 = extract_claim_details_from_text(QUERY_LETTER, source="insurer_query.pdf")
        self.assertEqual(facts2["nature_of_admission"], "Planned / Elective")

    def test_prefers_preauth_over_clinical_when_both_present(self):
        case = (
            f"=== Source document: insurer_query.pdf ===\n{QUERY_LETTER}\n\n"
            f"=== Source document: opd_note.pdf ===\n"
            "DOCUMENT TYPE: Handwritten consultation note\n"
            "BODY: Date: 4/6/2026 Name: Mr. Divyansh Mishra\n\n"
            f"=== Source document: hospital_form_scan.pdf ===\n{PRE_AUTH_GENERIC_FILENAME}"
        )
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["consultation_date"], "02/04/2025")
        self.assertEqual(facts["date_of_admission"], "30/06/2026")
        self.assertIn("handwritten", facts["consultation_date_source"].lower())


CASE165_LAB = """
=== Source document: CURRENT INVESTIGATION REPORT.pdf ===
DEPARTMENT OF BIOCHEMISTRY
RECEIVING DATE: 24/06/2026
REPORTING DATE: 24/06/2026
Nibedita Health Care
Gangapada Super Speciality Hospital Pvt. Ltd.
"""

CASE165_INDOOR = """
=== Source document: INDOOR CASE PAPER.pdf ===
INDOOR CASE PAPER
WARD / BED NO: ICU-3
Unstable Angina
Trop-T positive
chest discomfort x 3d
Date & Time: 19/06/2026
Gangapada Super Speciality Hospital Pvt. Ltd.
"""

CASE165_CT = """
=== Source document: CT SCAN.pdf ===
CT SCAN OF THORAX
Date: 19/06/2026
BED NO: ICU-3
"""


class Case165ClaimDetailsTests(unittest.TestCase):
    def test_lab_receiving_date_not_admission(self):
        facts = enrich_claim_facts(CASE165_LAB)
        self.assertNotEqual(facts["date_of_admission"], "24/06/2026")
        self.assertEqual(facts["date_of_admission"], "")

    def test_indoor_case_not_preauth_label(self):
        facts = enrich_claim_facts(CASE165_INDOOR)
        prov = facts.get("date_provenance") or {}
        sources = [
            e.get("source_label", "")
            for entries in prov.values()
            for e in (entries or [])
            if e.get("source_file") == "INDOOR CASE PAPER.pdf"
        ]
        self.assertTrue(sources)
        self.assertTrue(any("Indoor Case" in s for s in sources))
        self.assertFalse(any("Pre-Authorization" in s for s in sources))

    def test_emergency_nature_and_gangapada_hospital(self):
        case = CASE165_INDOOR + "\n" + CASE165_CT + "\n" + CASE165_LAB
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["nature_of_admission"], "Emergency")
        self.assertIn("Gangapada", facts["hospital"])

    def test_icu_imaging_date_used_when_no_explicit_admission(self):
        case = CASE165_CT + "\n" + CASE165_LAB
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["date_of_admission"], "19/06/2026")
        self.assertNotEqual(facts["date_of_admission"], "24/06/2026")

    def test_treatment_sheet_timestamp_not_consultation(self):
        chart = """
=== Source document: ward_chart_scan.pdf ===
INDOOR CASE PAPER
WARD / BED NO: ICU-3
Date & Time: 24/6/26
Unstable Angina
"""
        facts = enrich_claim_facts(chart)
        self.assertEqual(facts["consultation_date"], "")

    def test_lab_receiving_date_not_consultation(self):
        case = CASE165_LAB + "\n" + """
=== Source document: ward_chart_scan.pdf ===
INDOOR CASE PAPER
WARD / BED NO: ICU-3
Date & Time: 24/06/2026
"""
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["consultation_date"], "")


CASE68_QUERY = """
=== Source document: query_letter.pdf ===
QUERY LETTER    Date: 24 Jun 2026  Claim Incident : 2026062400280
Member Code : H1486808-3-1
Policy No : H1486808
Patient Name Vasudha Dnyanoba Bokde
"""

CASE68_POLICY_WORDING = """
=== Source document: Family Health Protector Policy Wording.pdf ===
Family Health Protector Policy Wording
MRI and CT scan coverage as per policy terms and magnetic resonance imaging benefits.
"""

CASE68_BILL = """
=== Source document: hospital_bill.pdf ===
Hospital Name: Ruby Hall Clinic
FINAL BILL SUMMARY
Total Hospital Bill: Rs. 1,45,680.00
Net Payable Amount: Rs. 1,40,000.00
"""

CASE68_PREAUTH = """
=== Source document: preauth.pdf ===
REQUEST FOR CASHLESS HOSPITALIZATION
Name of the Hospital: Ruby Hall Clinic
Date of first consultation: 15/03/2026
Policy No : H1486808
"""


class Case68ExtractorTests(unittest.TestCase):
    def test_policy_number_from_member_code(self):
        facts = extract_insurance_from_text(CASE68_QUERY)
        self.assertEqual(facts["policy_number"], "H1486808")

    def test_policy_wording_does_not_override_schedule(self):
        combined = CASE68_QUERY + "\n" + CASE68_POLICY_WORDING
        facts = enrich_insurance_facts(combined)
        self.assertEqual(facts["policy_number"], "H1486808")

    def test_hospital_and_bill_from_documents(self):
        case = CASE68_PREAUTH + "\n" + CASE68_BILL
        facts = enrich_claim_facts(case)
        self.assertIn("Ruby Hall", facts["hospital"])
        self.assertIn("Rs.", facts["total_hospital_bill"])

    def test_consult_date_not_query_letter_header(self):
        case = CASE68_QUERY + "\n" + CASE68_PREAUTH
        facts = enrich_claim_facts(case)
        self.assertEqual(facts["consultation_date"], "15/03/2026")

    def test_merge_financial_review_from_bill(self):
        facts = enrich_claim_facts(CASE68_BILL)
        result = {"claim_details": {}, "financial_review": {}}
        merge_claim_details_into_result(result, facts)
        self.assertIn("Rs.", result["financial_review"]["total_hospital_bill"])


if __name__ == "__main__":
    unittest.main()
