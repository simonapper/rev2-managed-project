from django.test import TestCase

from chats.services.derax.schema import empty_payload, validate_structural


class DeraxSchemaTests(TestCase):
    def test_empty_payload_is_schema_valid(self):
        payload = empty_payload()
        ok, errors = validate_structural(payload)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_artefact_items_must_be_objects(self):
        payload = empty_payload("EXECUTE")
        payload["artefacts"]["proposed"] = ["workbook draft"]
        ok, errors = validate_structural(payload)
        self.assertFalse(ok)
        self.assertTrue(any("artefacts.proposed[0]" in str(e) for e in errors))
