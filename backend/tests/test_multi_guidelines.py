"""Tests for multi-guideline selection and normalization."""

import unittest

from backend.services.audit_pipeline import _normalize_guideline_list


class MultiGuidelineTests(unittest.TestCase):
    def test_normalize_merges_single_and_list(self):
        result = _normalize_guideline_list("A.pdf", ["B.pdf", "A.pdf"])
        self.assertEqual(result, ["B.pdf", "A.pdf"])

    def test_normalize_single_only(self):
        self.assertEqual(_normalize_guideline_list("Ortho.pdf", None), ["Ortho.pdf"])

    def test_normalize_empty(self):
        self.assertEqual(_normalize_guideline_list(None, []), [])


if __name__ == "__main__":
    unittest.main()
