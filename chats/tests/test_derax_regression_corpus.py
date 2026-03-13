import json
from pathlib import Path

from django.test import TestCase

from chats.services.derax.validate import validate_derax_text


class DeraxRegressionCorpusTests(TestCase):
    def test_regression_corpus(self):
        path = Path("chats/tests/fixtures/derax_regression_corpus.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        failures = []
        for row in data:
            case_id = str((row or {}).get("id") or "").strip() or "unknown"
            expect_ok = bool((row or {}).get("expect_ok"))
            text = str((row or {}).get("text") or "")
            ok, _payload, errors = validate_derax_text(text)
            if ok != expect_ok:
                failures.append(
                    {
                        "id": case_id,
                        "expect_ok": expect_ok,
                        "actual_ok": ok,
                        "errors": list(errors or []),
                    }
                )

        self.assertEqual(
            failures,
            [],
            msg="Regression corpus mismatches: " + json.dumps(failures, ensure_ascii=True),
        )
