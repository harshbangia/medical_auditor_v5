"""Tests for durable follow-up QA session cache."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class QaSessionCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        import backend.services.qa_session_cache as cache

        self.cache = cache
        cache.clear_memory()
        cache.DISK_DIR = os.path.join(self.tmp.name, "qa_sessions")
        cache.TTL_SECONDS = 3600
        cache.MAX_DISK_SESSIONS = 50

    def test_put_get_roundtrip_memory(self):
        sid = "sess-abc"
        self.cache.put(
            sid,
            {
                "case_text": "Patient admitted for CABG",
                "guidelines": ["cardiac.pdf"],
                "guideline": "cardiac.pdf",
                "guideline_stores": [("cardiac.pdf", object(), ["chunk"])],
                "images": [],
            },
        )
        got = self.cache.get(sid)
        self.assertIsNotNone(got)
        self.assertEqual(got["case_text"], "Patient admitted for CABG")
        self.assertEqual(got["guidelines"], ["cardiac.pdf"])

    def test_survives_memory_clear_via_disk(self):
        sid = "sess-disk"
        self.cache.put(
            sid,
            {
                "case_text": "Handwritten preauth notes",
                "guidelines": ["neuro.pdf"],
                "guideline": "neuro.pdf",
                "images": [],
            },
        )
        path = self.cache._path(sid)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            disk = json.load(fh)
        self.assertEqual(disk["case_text"], "Handwritten preauth notes")
        self.assertNotIn("guideline_stores", disk)

        self.cache.clear_memory()

        fake_store = [("neuro.pdf", "index", ["c1"])]
        with patch.object(self.cache, "rebuild_guideline_stores", return_value=fake_store):
            got = self.cache.get(sid)
        self.assertIsNotNone(got)
        self.assertEqual(got["case_text"], "Handwritten preauth notes")
        self.assertEqual(got["guideline_stores"], fake_store)

    def test_missing_session_returns_none(self):
        self.assertIsNone(self.cache.get("does-not-exist"))

    def test_expired_disk_session_returns_none(self):
        sid = "sess-old"
        self.cache.put(
            sid,
            {"case_text": "old", "guidelines": [], "guideline": "", "images": []},
        )
        path = self.cache._path(sid)
        old = 1_000_000.0
        os.utime(path, (old, old))
        self.cache.clear_memory()
        self.cache.TTL_SECONDS = 10
        self.assertIsNone(self.cache.get(sid))


if __name__ == "__main__":
    unittest.main()
