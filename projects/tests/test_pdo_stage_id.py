from django.test import TestCase

from projects.services_artefacts import normalise_pdo_payload


class PdoStageIdTests(TestCase):
    def test_stage_id_added_from_stage_number(self):
        payload = {"stages": [{"stage_number": 2, "title": "T"}]}
        out = normalise_pdo_payload(payload)
        self.assertEqual(out["stages"][0]["stage_id"], "S2")

    def test_stage_id_preserved(self):
        payload = {"stages": [{"stage_number": 3, "stage_id": "S-ALPHA"}]}
        out = normalise_pdo_payload(payload)
        self.assertEqual(out["stages"][0]["stage_id"], "S-ALPHA")
