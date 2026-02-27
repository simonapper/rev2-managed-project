import json
import sys
import types
from unittest.mock import patch

from django.test import SimpleTestCase

from chats.services.derax.generate import generate_artefacts_from_execute_payload
from chats.services.derax.schema import empty_payload
from chats.services.llm import generate_derax


class DeraxGenerateTests(SimpleTestCase):
    def test_generate_derax_retries_then_succeeds(self):
        valid_payload = empty_payload(phase="DEFINE")
        valid_payload["intent"]["destination"] = "Launch DERAX v1"
        valid_payload["intent"]["constraints"] = ["No LLM integration in this slice"]
        valid_payload["intent"]["non_goals"] = ["No UI drawers yet"]
        valid_text = json.dumps(valid_payload)
        invalid_payload = empty_payload(phase="DEFINE")
        invalid_payload["intent"]["destination"] = "Launch DERAX v1"
        invalid_payload["intent"]["success_criteria"] = ["Should fail in DEFINE"]
        invalid_text = json.dumps(invalid_payload)

        calls = {"n": 0}

        def stub_llm_raw_text(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return invalid_text
            return valid_text

        out = generate_derax(
            user_text="Define destination",
            phase="DEFINE",
            project_id=1,
            chat_id=1,
            turn_id="t1",
            provider="openai",
            force_model="gpt-5.1",
            persist=False,
            compile_after=False,
            llm_raw_text_fn=stub_llm_raw_text,
        )
        self.assertEqual(calls["n"], 2)
        self.assertIn("payload", out)
        self.assertEqual(out.get("json_artefact_id"), "")
        self.assertEqual(out["payload"]["intent"]["destination"], "Launch DERAX v1")


class DeraxExecuteArtefactGenerationTests(SimpleTestCase):
    @patch("chats.services.derax.generate._persist_execute_artefact")
    @patch("chats.services.derax.generate._get_project")
    def test_generate_artefacts_creates_generated_entries_with_stub_store(self, mock_get_project, mock_persist):
        payload = empty_payload("EXECUTE")
        payload["meta"]["phase"] = "EXECUTE"
        payload["intent"]["destination"] = "Run a focused session."
        payload["artefacts"]["proposed"] = [
            {"kind": "run_sheet", "title": "Session run sheet", "notes": "Keep concise"},
            {"kind": "checklist", "title": "Session checklist", "notes": ""},
        ]

        class _Doc:
            def __init__(self, idx):
                self.id = idx

        mock_get_project.return_value = object()
        mock_persist.side_effect = [_Doc(101), _Doc(102)]

        out = generate_artefacts_from_execute_payload(
            project_id=1,
            chat_id=2,
            turn_id="t-exec-1",
            payload=payload,
            user_id=7,
        )

        self.assertEqual(len(out.get("generated") or []), 2)
        self.assertEqual(len(payload["artefacts"]["generated"]), 2)
        self.assertEqual(payload["artefacts"]["generated"][0]["artefact_id"], "101")
        self.assertEqual(payload["artefacts"]["generated"][1]["artefact_id"], "102")

    def test_generate_requires_execute_phase(self):
        payload = empty_payload("REFINE")
        payload["meta"]["phase"] = "REFINE"
        payload["artefacts"]["proposed"] = [{"kind": "run_sheet", "title": "x", "notes": ""}]

        with self.assertRaises(ValueError):
            generate_artefacts_from_execute_payload(
                project_id=1,
                chat_id=2,
                turn_id="t-refine-1",
                payload=payload,
                user_id=7,
            )

    @patch("chats.services.derax.generate._persist_execute_artefact")
    @patch("chats.services.derax.generate._get_project")
    def test_generate_normalises_string_proposals(self, mock_get_project, mock_persist):
        payload = empty_payload("EXECUTE")
        payload["meta"]["phase"] = "EXECUTE"
        payload["intent"]["destination"] = "Run a focused session."
        payload["artefacts"]["proposed"] = [
            "2-hour session workbook/slide-deck run-of-show with prompts",
        ]

        class _Doc:
            def __init__(self, idx):
                self.id = idx

        mock_get_project.return_value = object()
        mock_persist.side_effect = [_Doc(201), _Doc(202)]

        out = generate_artefacts_from_execute_payload(
            project_id=1,
            chat_id=2,
            turn_id="t-exec-2",
            payload=payload,
            user_id=7,
        )

        self.assertGreaterEqual(len(out.get("generated") or []), 1)
        self.assertIsInstance(payload["artefacts"]["proposed"][0], dict)

    @patch("chats.services.derax.generate.build_xlsx_for_kind", return_value=b"xlsx")
    @patch("chats.services.derax.generate._persist_execute_artefact")
    @patch("chats.services.derax.generate._get_project")
    def test_generate_run_sheet_creates_xlsx_when_library_available(self, mock_get_project, mock_persist, _mock_xlsx):
        payload = empty_payload("EXECUTE")
        payload["meta"]["phase"] = "EXECUTE"
        payload["intent"]["destination"] = "Run a focused session."
        payload["artefacts"]["proposed"] = [{"kind": "run_sheet", "title": "Session run sheet", "notes": ""}]

        class _Doc:
            def __init__(self, idx):
                self.id = idx

        mock_get_project.return_value = object()
        mock_persist.side_effect = [_Doc(301), _Doc(302)]

        with patch.dict(sys.modules, {"openpyxl": types.ModuleType("openpyxl")}):
            out = generate_artefacts_from_execute_payload(
                project_id=1,
                chat_id=2,
                turn_id="t-exec-3",
                payload=payload,
                user_id=7,
            )

        titles = [str(x.get("title") or "") for x in list(out.get("generated") or [])]
        self.assertIn("Session run sheet", titles)
        self.assertIn("Session run sheet (xlsx)", titles)
