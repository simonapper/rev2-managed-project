from django.test import SimpleTestCase

from projects.services_execute_validator import merge_execute_update, validate_execute_update


class ExecuteValidatorTests(SimpleTestCase):
    def test_validate_rejects_stage_change(self):
        route = {"stages": [{"stage_id": "S1"}, {"stage_id": "S2"}]}
        current = {"stages": [{"stage_id": "S1"}, {"stage_id": "S2"}], "decisions": []}
        proposed = {
            "marker": "EXECUTE",
            "artefact_type": "EXECUTION_STATE",
            "version": 1,
            "source_route": {},
            "outputs": [],
            "stages": [{"stage_id": "S1"}],
        }
        ok, errs = validate_execute_update(route, current, proposed)
        self.assertFalse(ok)
        self.assertTrue(errs)

    def test_validate_rejects_stage_id_change(self):
        route = {"stages": [{"stage_id": "S1"}]}
        current = {"stages": [{"stage_id": "S1"}], "decisions": []}
        proposed = {
            "marker": "EXECUTE",
            "artefact_type": "EXECUTION_STATE",
            "version": 1,
            "source_route": {},
            "outputs": [],
            "stages": [{"stage_id": "S9"}],
        }
        ok, _errs = validate_execute_update(route, current, proposed)
        self.assertFalse(ok)

    def test_validate_rejects_decision_drop(self):
        route = {"stages": [{"stage_id": "S1"}]}
        current = {"stages": [{"stage_id": "S1"}], "decisions": ["d1"]}
        proposed = {
            "marker": "EXECUTE",
            "artefact_type": "EXECUTION_STATE",
            "version": 1,
            "source_route": {},
            "outputs": [],
            "stages": [{"stage_id": "S1"}],
        }
        ok, _errs = validate_execute_update(route, current, proposed)
        self.assertFalse(ok)

    def test_merge_preserves_decisions(self):
        route = {"stages": [{"stage_id": "S1"}]}
        current = {"stages": [{"stage_id": "S1"}], "decisions": ["d1"]}
        proposed = {
            "marker": "EXECUTE",
            "artefact_type": "EXECUTION_STATE",
            "version": 1,
            "source_route": {},
            "outputs": [],
            "stages": [{"stage_id": "S1"}],
        }
        merged = merge_execute_update(route, current, proposed)
        self.assertEqual(merged.get("decisions"), ["d1"])
