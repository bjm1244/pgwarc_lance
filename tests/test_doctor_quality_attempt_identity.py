import decimal
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
for directory in (TOOLS, TESTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

DOCTOR_PATH = TOOLS / "doctor_quality_attempt_identity.py"
SPEC = importlib.util.spec_from_file_location(
    "doctor_quality_attempt_identity", DOCTOR_PATH
)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = doctor
SPEC.loader.exec_module(doctor)

EVAL_PATH = TOOLS / "eval_quality.py"
EVAL_SPEC = importlib.util.spec_from_file_location("eval_quality", EVAL_PATH)
eval_quality = importlib.util.module_from_spec(EVAL_SPEC)
assert EVAL_SPEC and EVAL_SPEC.loader
sys.modules[EVAL_SPEC.name] = eval_quality
EVAL_SPEC.loader.exec_module(eval_quality)


CONTRACT_FIXTURE = (
    ROOT / "tests" / "fixtures" / "quality_attempt_identity_contract_cases.json"
)
CONTRACT_SCHEMA = ROOT / "contracts" / "quality_attempt_identity.v1.schema.json"
FORBIDDEN_IDENTITY_KEYS = (
    "attempt_id",
    "capture_state",
    "capture_source",
    "session_identity",
    "transaction_identity",
)


def load_contract_cases() -> dict:
    return json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))


def load_contract_schema() -> dict:
    return json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))


def contract_case(name: str) -> dict:
    for case in load_contract_cases()["cases"]:
        if case["name"] == name:
            return case
    raise AssertionError(f"contract fixture case not found: {name}")


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def valid_captured_envelope() -> dict:
    return {
        "schema_version": 1,
        "attempt_id": "attempt-cli-0001",
        "capture_state": "captured",
        "session_identity": {
            "backend_pid": 321,
            "backend_start": "2026-09-15T03:00:00+09:00",
        },
        "transaction_identity": {"state": "unobserved"},
        "capture_source": "query_session",
    }


def valid_unavailable_envelope() -> dict:
    return {
        "schema_version": 1,
        "attempt_id": "attempt-cli-0002",
        "capture_state": "unavailable",
        "session_identity": None,
        "transaction_identity": {"state": "unobserved"},
        "capture_source": "none",
    }


def run_doctor(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = doctor.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class QualityAttemptIdentityContractTests(unittest.TestCase):
    def test_fixture_has_valid_and_invalid_cases(self):
        fixture = load_contract_cases()
        cases = fixture["cases"]
        self.assertTrue(cases)
        self.assertGreaterEqual(len([c for c in cases if c["valid"]]), 2)
        self.assertGreaterEqual(len([c for c in cases if not c["valid"]]), 8)
        names = [case["name"] for case in cases]
        self.assertEqual(len(names), len(set(names)))

    def test_fixture_cases_match_validator(self):
        fixture = load_contract_cases()
        label = fixture["label"]
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                errors = doctor.validate_identity(case["envelope"], label=label)
                if case["valid"]:
                    self.assertEqual([], errors)
                    self.assertEqual([], case["expected_errors"])
                    continue
                self.assertTrue(errors, case["name"])
                for expected in case["expected_errors"]:
                    self.assertIn(expected, errors)

    def test_non_integer_schema_version_rejected_by_validator_and_schema(self):
        case = contract_case("non_integer_schema_version")
        label = load_contract_cases()["label"]
        version = case["envelope"]["schema_version"]
        self.assertIsInstance(version, float)
        self.assertEqual(1.0, version)

        errors = doctor.validate_identity(case["envelope"], label=label)
        self.assertIn(f"{label}.schema_version must be 1", errors)

        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")

        schema = load_contract_schema()
        declared = schema["properties"]["schema_version"]
        self.assertEqual("integer", declared["type"])
        self.assertEqual(1, declared["const"])

        payload = json.loads(
            json.dumps(case["envelope"]), parse_float=decimal.Decimal
        )
        self.assertIsInstance(payload["schema_version"], decimal.Decimal)
        schema_errors = list(
            jsonschema.Draft202012Validator(schema).iter_errors(payload)
        )
        self.assertTrue(
            any(
                error.validator == "type"
                and list(error.absolute_path) == ["schema_version"]
                for error in schema_errors
            ),
            schema_errors,
        )

    def test_default_label_is_stable(self):
        errors = doctor.validate_identity([])
        self.assertEqual(["quality_attempt_identity must be an object"], errors)

    def test_bool_is_not_an_integer_pid(self):
        self.assertFalse(doctor.is_positive_integer(True))
        self.assertFalse(doctor.is_positive_integer(False))
        self.assertTrue(doctor.is_positive_integer(1))

    def test_timezone_bearing_timestamp_requires_offset(self):
        self.assertTrue(
            doctor.is_explicit_timezone_datetime("2026-09-15T03:00:00+09:00")
        )
        self.assertTrue(doctor.is_explicit_timezone_datetime("2026-09-15T03:00:00Z"))
        self.assertFalse(doctor.is_explicit_timezone_datetime("2026-09-15T03:00:00"))
        self.assertFalse(doctor.is_explicit_timezone_datetime("2026-09-15"))
        self.assertFalse(doctor.is_explicit_timezone_datetime("not-a-timestamp"))

    def test_transaction_identity_extra_field_is_rejected(self):
        envelope = valid_captured_envelope()
        envelope["transaction_identity"]["transaction_id"] = "1"
        errors = doctor.validate_identity(envelope)
        self.assertIn(
            "quality_attempt_identity.transaction_identity must not include "
            "unsupported fields: transaction_id",
            errors,
        )


class QualityAttemptIdentityCliTests(unittest.TestCase):
    def test_valid_envelope_exits_zero_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "identity.json"
            json_output = tmp_path / "identity_doctor.json"
            markdown_output = tmp_path / "identity_doctor.md"
            write_json(input_path, valid_captured_envelope())

            exit_code, stdout, stderr = run_doctor(
                [
                    "--input-json",
                    str(input_path),
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                ]
            )

            self.assertEqual(0, exit_code, stderr)
            self.assertIn("valid=true", stdout)
            report_doc = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertTrue(report_doc["valid"])
            self.assertEqual(0, report_doc["error_count"])
            self.assertEqual(321, report_doc["backend_pid"])
            markdown = markdown_output.read_text(encoding="utf-8")
            self.assertIn("# pgwarc_lance Quality Attempt Identity Doctor", markdown)
            self.assertIn("A structurally valid envelope is synthetic and opt-in.", markdown)

    def test_unavailable_envelope_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "identity.json"
            write_json(input_path, valid_unavailable_envelope())
            exit_code, stdout, stderr = run_doctor(["--input-json", str(input_path)])
            self.assertEqual(0, exit_code, stderr)
            self.assertIn("valid=true", stdout)

    def test_invalid_envelope_exits_nonzero_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "identity.json"
            json_output = tmp_path / "identity_doctor.json"
            markdown_output = tmp_path / "identity_doctor.md"
            envelope = valid_captured_envelope()
            envelope["session_identity"]["backend_pid"] = 0
            envelope["capture_source"] = "none"
            write_json(input_path, envelope)

            exit_code, stdout, stderr = run_doctor(
                [
                    "--input-json",
                    str(input_path),
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                ]
            )

            self.assertEqual(1, exit_code)
            self.assertIn("valid=false", stdout)
            self.assertIn("errors=", stdout)
            report_doc = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertFalse(report_doc["valid"])
            self.assertGreaterEqual(report_doc["error_count"], 1)
            self.assertIn(
                "quality_attempt_identity.session_identity.backend_pid must be a "
                "positive integer",
                report_doc["errors"],
            )
            markdown = markdown_output.read_text(encoding="utf-8")
            self.assertIn("## Errors", markdown)
            self.assertIn("backend_pid must be a positive integer", markdown)
            self.assertIn("quality attempt identity doctor failed:", stderr)

    def test_missing_input_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            exit_code, stdout, stderr = run_doctor(["--input-json", str(missing)])
            self.assertEqual(1, exit_code)
            self.assertIn("valid=false", stdout)
            self.assertIn("could not load input JSON", stderr)

    def test_malformed_json_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "bad.json"
            input_path.write_text("{ not json", encoding="utf-8")
            exit_code, _stdout, stderr = run_doctor(["--input-json", str(input_path)])
            self.assertEqual(1, exit_code)
            self.assertIn("could not load input JSON", stderr)


class QualityAttemptIdentityIsolationTests(unittest.TestCase):
    def test_validator_does_not_alter_timeout_or_eval_artifacts(self):
        payload = eval_quality.timeout_payload(
            fixture={"name": "f", "docs": [], "queries": [], "vector_dim": 4},
            results=[],
            k=3,
            lance_uri="/tmp/isolation.lance",
            stage="query",
            limit_seconds=5.0,
            client_side_only=True,
        )
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact_path = tmp_path / "quality.json"
            artifact_path.write_text(serialized, encoding="utf-8")
            input_path = tmp_path / "identity.json"
            write_json(input_path, valid_captured_envelope())

            exit_code, _stdout, stderr = run_doctor(
                ["--input-json", str(input_path)]
            )
            self.assertEqual(0, exit_code, stderr)
            self.assertEqual(serialized, artifact_path.read_text(encoding="utf-8"))

        observation = payload["timeout"]["timeout_observation"]
        self.assertEqual(
            set(eval_quality.TIMEOUT_OBSERVATION_FIELDS), set(observation)
        )
        self.assertEqual(
            {
                "statement_outcome": "unknown",
                "backend_state_after_timeout": "unobserved",
                "cancellation_completion": "unverified",
                "transaction_cleanup": "unverified",
            },
            observation,
        )
        blob = json.dumps(payload)
        for forbidden in FORBIDDEN_IDENTITY_KEYS:
            self.assertNotIn(forbidden, blob)

    def test_validator_is_not_wired_into_normal_paths(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        verify_line = next(
            line for line in makefile.splitlines() if line.startswith("verify:")
        )
        self.assertNotIn("attempt", verify_line)
        test_all_line = next(
            line for line in makefile.splitlines() if line.startswith("test-all:")
        )
        self.assertNotIn("attempt", test_all_line)
        self.assertNotIn(
            "doctor_quality_attempt_identity",
            (ROOT / "tools" / "eval_quality.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "doctor_quality_attempt_identity",
            (ROOT / "tools" / "doctor_run_directory.py").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(
            doctor.__file__,
            "the doctor module must remain a standalone opt-in tool",
        )


if __name__ == "__main__":
    unittest.main()
