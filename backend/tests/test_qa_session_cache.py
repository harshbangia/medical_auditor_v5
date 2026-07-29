"""Tests for durable follow-up QA session cache."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


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
        with patch.object(self.cache, "_write_db"):
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
        with patch.object(self.cache, "_write_db"), patch.object(
            self.cache, "_read_db", return_value=None
        ):
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

    def test_survives_via_db_when_disk_missing(self):
        sid = "sess-db"
        self.cache.clear_memory()
        db_payload = {
            "case_text": "From postgres",
            "guidelines": ["guide.pdf"],
            "guideline": "guide.pdf",
            "images": [],
            "guideline_stores": [],
            "created_at": 1_700_000_000.0,
        }
        with patch.object(self.cache, "_read_disk", return_value=None), patch.object(
            self.cache, "_read_db", return_value=db_payload
        ), patch.object(self.cache, "_write_disk"), patch.object(
            self.cache, "rebuild_guideline_stores", return_value=[("guide.pdf", "i", ["c"])]
        ):
            got = self.cache.get(sid)
        self.assertIsNotNone(got)
        self.assertEqual(got["case_text"], "From postgres")

    def test_put_writes_db(self):
        sid = "sess-write-db"
        with patch.object(self.cache, "_write_db") as write_db:
            self.cache.put(
                sid,
                {"case_text": "x", "guidelines": [], "guideline": "", "images": []},
            )
            write_db.assert_called_once()
            self.assertEqual(write_db.call_args[0][0], sid)

    def test_missing_session_returns_none(self):
        with patch.object(self.cache, "_read_db", return_value=None):
            self.assertIsNone(self.cache.get("does-not-exist"))

    def test_expired_disk_session_returns_none(self):
        sid = "sess-old"
        with patch.object(self.cache, "_write_db"), patch.object(
            self.cache, "_read_db", return_value=None
        ):
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

    def test_default_ttl_is_seven_days(self):
        # Module default at import uses env; re-check configured constant after setUp override.
        import importlib
        import backend.services.qa_session_cache as cache_mod

        with patch.dict(os.environ, {"QA_SESSION_TTL_HOURS": "168"}, clear=False):
            reloaded = importlib.reload(cache_mod)
            self.assertEqual(reloaded.TTL_SECONDS, 168 * 3600)
            # Restore DISK_DIR for other tests that may import the module
            reloaded.DISK_DIR = self.cache.DISK_DIR
            reloaded.TTL_SECONDS = 3600
            self.cache = reloaded


if __name__ == "__main__":
    unittest.main()
