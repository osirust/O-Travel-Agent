import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aegml.config import Config, ConfigurationError, IntegrationNotConfigured, create_language_model
from aegml.service import prepare_demo
from aegml.validation import validate_request


ROOT = Path(__file__).resolve().parents[1]


def valid_request(**changes):
    data = {
        "destination": "Казань",
        "check_in": "2026-11-06",
        "check_out": "2026-11-09",
        "adults": 2,
        "max_total_rub": 18000,
    }
    return {**data, **changes}


class RequestTests(unittest.TestCase):
    def test_budget_applies_to_entire_stay(self):
        result, code = prepare_demo(valid_request(), Config())
        self.assertEqual(code, 0)
        self.assertEqual([offer["total_rub"] for offer in result["offers"]], [14400])
        self.assertEqual(result["request"]["adults"], 2)

    def test_missing_dates_ask_questions_without_inventing_values(self):
        result, code = prepare_demo({"destination": "Сочи", "adults": 2}, Config())
        self.assertEqual((code, result["status"]), (0, "needs_clarification"))
        self.assertEqual(len(result["questions"]), 2)
        self.assertEqual(result["offers"], [])
        self.assertNotIn("request", result)

    def test_optional_budget_remains_unset(self):
        data = valid_request()
        del data["max_total_rub"]
        result, _ = prepare_demo(data, Config())
        self.assertIsNone(result["request"]["max_total_rub"])
        self.assertEqual(len(result["offers"]), 2)

    def test_invalid_dates_and_order(self):
        for changes in (
            {"check_in": "2026-02-30"},
            {"check_in": "20261106"},
            {"check_out": "2026-11-06"},
            {"check_out": "2026-11-05"},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(validate_request(valid_request(**changes)).errors)

    def test_budget_and_adults_require_positive_integers(self):
        for field in ("max_total_rub", "adults"):
            for value in (0, -1, True, 1.5, "100"):
                with self.subTest(field=field, value=value):
                    self.assertTrue(validate_request(valid_request(**{field: value})).errors)

    def test_non_object_and_unknown_fields_rejected(self):
        for data in ([], "Сочи", None, valid_request(book_now=True)):
            with self.subTest(data=data):
                result, code = prepare_demo(data, Config())
                self.assertEqual((code, result["status"]), (2, "invalid_request"))

    def test_unsupported_city_never_changes_destination(self):
        result, _ = prepare_demo(valid_request(destination="Париж"), Config())
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["offers"], [])
        self.assertIn("Казань и Сочи", result["questions"][0])

    def test_demo_cannot_claim_real_availability_or_booking(self):
        result, _ = prepare_demo(valid_request(), Config())
        self.assertTrue(result["is_demo"])
        self.assertEqual(result["booking"], {"status": "not_supported", "created": False})
        self.assertFalse(result["llm"]["called"])
        for offer in result["offers"]:
            self.assertTrue(offer["is_demo"])
            self.assertTrue(offer["name"].startswith("DEMO"))
            self.assertEqual(offer["availability"], "demo_only_not_verified")


class IntegrationTests(unittest.TestCase):
    def test_local_and_api_never_fall_back(self):
        for mode in ("local", "api"):
            with self.subTest(mode=mode):
                with self.assertRaises(IntegrationNotConfigured):
                    create_language_model(Config(llm_mode=mode))
                with self.assertRaises(IntegrationNotConfigured):
                    prepare_demo(valid_request(), Config(llm_mode=mode))

    def test_unknown_mode_is_error(self):
        with self.assertRaises(ConfigurationError):
            Config.from_env({"AEGML_LLM_MODE": "typo"})

    def test_configuration_does_not_load_env_files(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("AEGML_LLM_MODE=api", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(Config.from_env({}).llm_mode, "mock")
            finally:
                os.chdir(previous)

    def run_cli(self, contents, *, mode="mock"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "request.json")
            path.write_text(contents, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "aegml", "--request", str(path)],
                cwd=ROOT,
                env={**os.environ, "AEGML_LLM_MODE": mode},
                capture_output=True,
                encoding="utf-8",
                check=False,
            )

    def test_malformed_input_is_readable_json_error(self):
        for contents in ('{"destination":', '{"max_total_rub": NaN}'):
            with self.subTest(contents=contents):
                process = self.run_cli(contents)
                self.assertEqual(process.returncode, 2)
                payload = json.loads(process.stdout)
                self.assertEqual(payload["status"], "input_error")
                self.assertIn("Не удалось прочитать", payload["errors"][0])
                self.assertEqual(process.stderr, "")

    def test_cli_api_mode_is_explicit_failure(self):
        process = self.run_cli(json.dumps(valid_request()), mode="api")
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "integration_not_configured")
        self.assertNotIn("offers", payload)


if __name__ == "__main__":
    unittest.main()
