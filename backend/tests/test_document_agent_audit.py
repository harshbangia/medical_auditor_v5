"""Tests for Gemini document-agent audit helpers."""

from __future__ import annotations

import unittest

from backend.services.document_agent_audit import _extract_meta, _strip_fences, audit_pipeline_mode


SAMPLE_HTML = """
<!DOCTYPE html><html><body>
<td class="col-label">Claim Reference</td>
<td class="col-value">2026071700347 / Sub: 2026071700347.R1</td>
<td class="col-label">Patient Name</td>
<td class="col-value">Mrs. Bency Biju (40Y / Female)</td>
<td class="col-label">Policy Number / Type</td>
<td class="col-value">H1677679 / Family Health Protector</td>
<td class="col-label">Hospital / Location</td>
<td class="col-value">Daya General Hospital Ltd., Thrissur</td>
<td class="col-label">Admission / Discharge</td>
<td class="col-value">17/07/2026 (10:51 AM) to 20/07/2026 (03:21 PM)</td>
<td class="col-label">Primary Diagnosis</td>
<td class="col-value">Right Hemispheric TIA</td>
<td class="col-label">Total Claimed / Billed</td>
<td class="col-value">₹54,223.03</td>
<td class="col-label">Final Audit Decision</td>
<td class="col-value">Partially Approve (Overrule Denial)</td>
</body></html>
"""


class TestDocumentAgentHelpers(unittest.TestCase):
    def test_strip_fences(self):
        raw = "```html\n<html>ok</html>\n```"
        self.assertIn("<html>", _strip_fences(raw))

    def test_extract_meta(self):
        meta = _extract_meta(SAMPLE_HTML)
        self.assertIn("Bency", meta["patient_details"]["name"])
        self.assertEqual(meta["patient_details"]["age"], "40")
        self.assertEqual(meta["patient_details"]["sex"], "Female")
        self.assertEqual(meta["insurance_details"]["policy_number"], "H1677679")
        self.assertEqual(meta["insurance_details"]["claim_incident_number"], "2026071700347")
        self.assertIn("Daya", meta["claim_details"]["hospital"])
        self.assertEqual(meta["claim_recommended"], "Yes")
        self.assertEqual(meta["compliance_verdict"], "Partially Compliant")

    def test_pipeline_mode_default(self):
        # function reads env; just ensure it returns a known token
        mode = audit_pipeline_mode()
        self.assertIn(mode, {"legacy", "document_agent"})


if __name__ == "__main__":
    unittest.main()
