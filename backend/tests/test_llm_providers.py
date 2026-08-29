"""Unit tests for LLM provider abstraction (no live API calls)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from backend.llm.base import ImageInput
from backend.llm.models import (
    GEMINI_AUDIT_DEFAULT,
    GEMINI_FLASH_DEFAULT,
    get_provider_name,
    resolve_models,
)
from backend.llm_client import reset_llm_provider


class TestModelResolution(unittest.TestCase):
    def tearDown(self):
        reset_llm_provider()

    def test_gemini_defaults_ignore_gpt_env(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "gemini",
                "AUDIT_MODEL": "gpt-4o",
                "VISION_MODEL": "gpt-4o-mini",
                "VISION_OCR_MODEL": "gpt-4o",
                "EMBEDDING_MODEL": "text-embedding-3-small",
            },
            clear=False,
        ):
            models = resolve_models("gemini")
            self.assertEqual(models["audit"], GEMINI_AUDIT_DEFAULT)
            self.assertEqual(models["vision"], GEMINI_FLASH_DEFAULT)
            self.assertEqual(models["vision_ocr"], GEMINI_AUDIT_DEFAULT)
            self.assertTrue(models["embedding"].startswith("gemini"))

    def test_retired_gemini_25_remapped(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "gemini",
                "AUDIT_MODEL": "gemini-2.5-pro",
                "VISION_MODEL": "gemini-2.5-flash",
                "VISION_OCR_MODEL": "models/gemini-2.5-pro",
                "EXTRACT_MODEL": "gemini-2.5-flash",
            },
            clear=False,
        ):
            models = resolve_models("gemini")
            self.assertEqual(models["audit"], GEMINI_AUDIT_DEFAULT)
            self.assertEqual(models["vision"], GEMINI_FLASH_DEFAULT)
            self.assertEqual(models["vision_ocr"], GEMINI_AUDIT_DEFAULT)
            self.assertEqual(models["extract"], GEMINI_FLASH_DEFAULT)

    def test_openai_defaults(self):
        models = resolve_models("openai")
        self.assertIn("gpt", models["audit"])
        self.assertIn("embedding", models["embedding"])

    def test_provider_name(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}, clear=False):
            self.assertEqual(get_provider_name(), "gemini")
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=False):
            self.assertEqual(get_provider_name(), "openai")


class TestOpenAIProviderComplete(unittest.TestCase):
    def tearDown(self):
        reset_llm_provider()

    def test_complete_json_uses_responses_api(self):
        from backend.llm.openai_provider import OpenAIProvider

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.output_text = '{"ok": true}'
        mock_client.responses.create.return_value = mock_resp

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            with patch("backend.llm.openai_provider.OpenAI", return_value=mock_client):
                p = OpenAIProvider(api_key="sk-test")
                # Replace client after init
                p._client = mock_client
                text = p.complete(
                    model="gpt-4o-mini",
                    text_parts=["Return JSON"],
                    json_mode=True,
                )
        self.assertEqual(text, '{"ok": true}')
        mock_client.responses.create.assert_called_once()
        kwargs = mock_client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["text"]["format"]["type"], "json_object")

    def test_vision_sends_image_part(self):
        from backend.llm.openai_provider import OpenAIProvider

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.output_text = "Page 1 transcribed"
        mock_client.responses.create.return_value = mock_resp
        # tiny jpeg-ish base64
        b64 = "AAAA"

        p = OpenAIProvider.__new__(OpenAIProvider)
        p._client = mock_client
        text = p.complete(
            model="gpt-4o",
            text_parts=["Transcribe"],
            images=[ImageInput(b64=b64, detail="high")],
        )
        self.assertEqual(text, "Page 1 transcribed")
        content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
        types = [c["type"] for c in content]
        self.assertIn("input_image", types)


class TestGeminiProviderComplete(unittest.TestCase):
    def tearDown(self):
        reset_llm_provider()

    def test_complete_json_sets_mime(self):
        from backend.llm.gemini_provider import GeminiProvider

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = '{"ok": true}'
        mock_client.models.generate_content.return_value = mock_resp

        p = GeminiProvider.__new__(GeminiProvider)
        p._client = mock_client
        text = p.complete(
            model="gemini-3.6-flash",
            text_parts=["Return JSON"],
            json_mode=True,
            temperature=0.2,
        )
        self.assertEqual(text, '{"ok": true}')
        kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-3.6-flash")
        config = kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        # Gemini 3.x: do not pass temperature (API may reject it)
        self.assertIsNone(getattr(config, "temperature", None))


class TestMalformedJsonPath(unittest.TestCase):
    def test_audit_json_parser_tolerates_fences(self):
        from backend.ai.audit_engine import _parse_audit_json

        raw = '```json\n{"compliance_verdict": "Non-Compliant", "observations": []}\n```'
        data = _parse_audit_json(raw)
        self.assertEqual(data.get("compliance_verdict"), "Non-Compliant")
        self.assertNotIn("error", data)


if __name__ == "__main__":
    unittest.main()
