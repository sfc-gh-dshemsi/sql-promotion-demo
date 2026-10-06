"""Offline checks only. Snowflake calls are replaced by an in-memory fake."""

import copy
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


def module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = module_from_path("deploy", ROOT / "ci/deploy.py")
setup = module_from_path("setup_render", ROOT / "setup/render.py")
COMMIT = "a" * 40


def result_rows(version="v1"):
    fixture = json.loads((ROOT / "fixtures/orders.json").read_text())
    selected = [order for order in fixture if version == "v1" or order["status"] != "cancelled"]
    rows = [{"ROW_KIND": "ORDER", "ORDER_ID": order["order_id"], "STATUS": order["status"],
             "AMOUNT": order["amount"], "ORDER_COUNT": None, "TOTAL_AMOUNT": None,
             "RULES_VERSION": version, "RUN_TOKEN": "baseline-token"} for order in selected]
    rows.append({"ROW_KIND": "SUMMARY", "ORDER_ID": None, "STATUS": None, "AMOUNT": None,
                 "ORDER_COUNT": len(selected),
                 "TOTAL_AMOUNT": str(sum(Decimal(order["amount"]) for order in selected)),
                 "RULES_VERSION": version, "RUN_TOKEN": "baseline-token"})
    return rows


class FakeCLI:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.reads = 0

    def preflight(self):
        self.calls.append("PREFLIGHT")
        if self.failure == "context":
            raise ValueError("wrong context")

    def run(self, sql, rows=False):
        self.calls.append(sql)
        if "TASK_HISTORY" in sql:
            if "TO_TIMESTAMP_LTZ" not in sql:
                return [{"STATE": "EXECUTING"}] if self.failure == "busy" else []
            if self.failure == "timeout":
                return []
            return [{"STATE": "FAILED" if self.failure == "task" else "SUCCEEDED"}]
        if "AS STARTED" in sql:
            return [{"STARTED": 1234567890000}]
        if "CREATE OR REPLACE PROCEDURE" in sql and self.failure == "deploy":
            raise subprocess.CalledProcessError(1, "snow")
        if sql.startswith("CALL"):
            if self.failure == "missing_exception":
                return None
            message = "network failure" if self.failure == "wrong_exception" else "DEMO_INJECTED_FAILURE"
            raise subprocess.CalledProcessError(1, "snow", stderr=message)
        if "'ORDER' AS ROW_KIND" in sql:
            self.reads += 1
            result = result_rows(json.loads((driver.ROOT / "release.json").read_text())["version"])
            if self.failure == "checks":
                result[-1]["TOTAL_AMOUNT"] = "99"
            if self.failure == "rollback" and self.reads > 1:
                result[0]["RUN_TOKEN"] = "changed"
            return result
        if "INSERT INTO" in sql and "RELEASE_LOG" in sql and self.failure == "log":
            raise subprocess.CalledProcessError(1, "snow")
        if sql.endswith(" RESUME") and self.failure == "resume":
            raise subprocess.CalledProcessError(1, "snow")
        return None


class WorkloadTests(unittest.TestCase):
    def test_v1_and_v2_select_actual_fixture_rows(self):
        # Execute only the portable SELECT from the real procedure in SQLite.
        # This tests business logic, not Snowflake syntax or transaction semantics.
        for prefix in ("", "examples/v2/"):
            release = json.loads((ROOT / prefix / "release.json").read_text())
            total, count = Decimal(release["expected_total"]), release["expected_count"]
            sql = driver.render(prefix + "pipelines/silver/orders.sql", "QA")
            select = re.search(r"SELECT ORDER_ID, LOWER\(TRIM\(STATUS\)\).*?;", sql, re.S).group(0)
            select = select.replace("PROMOTION_SOURCE.RAW.ORDERS", "raw_orders")
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE TABLE raw_orders(order_id TEXT, status TEXT, amount NUMERIC)")
                fixture = json.loads((ROOT / "fixtures/orders.json").read_text())
                connection.executemany("INSERT INTO raw_orders VALUES (?, ?, ?)",
                                       [(row["order_id"], row["status"], row["amount"]) for row in fixture])
                rows = connection.execute(select, {"run_token": "test"}).fetchall()
            self.assertEqual(len(rows), count)
            self.assertEqual(sum(Decimal(str(row[2])) for row in rows), total)
            self.assertEqual(sorted(row[0] for row in rows), sorted(release["expected_order_ids"]))
            self.assertEqual({row[3] for row in rows}, {release["version"]})
            driver.check_results(result_rows(release["version"]), release)

    def test_transaction_is_explicit_and_has_no_ddl_inside(self):
        for prefix in ("", "examples/v2/"):
            sql = driver.render(prefix + "pipelines/silver/orders.sql", "QA")
            transaction = sql.split("BEGIN TRANSACTION;", 1)[1]
            self.assertNotRegex(transaction, r"\b(CREATE|ALTER|DROP|TRUNCATE)\b")
            self.assertIn("ROLLBACK;\n            RAISE;", transaction)
            self.assertLess(transaction.index("RAISE injected_failure"), transaction.index("COMMIT;"))
            self.assertIn(":run_token", transaction)

    def test_expectation_checker_rejects_missing_duplicate_wrong_and_mixed_rows(self):
        release = json.loads((ROOT / "release.json").read_text())
        mutations = [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                     lambda rows: rows[0].update(AMOUNT="999"),
                     lambda rows: rows[0].update(RULES_VERSION="unexpected-version"),
                     lambda rows: rows[0].update(RUN_TOKEN="different"),
                     lambda rows: rows[-1].update(ORDER_COUNT=0)]
        for mutate in mutations:
            rows = result_rows(release["version"])
            mutate(rows)
            with self.assertRaises(ValueError):
                driver.check_results(rows, release)

    def test_all_templates_render_for_both_environments(self):
        for environment in ("QA", "PROD"):
            for path in (*driver.PIPELINE_FILES, "checks/results.sql"):
                sql = driver.render(path, environment)
                self.assertNotIn("{{", sql)
                self.assertNotIn("{%", sql)
                other = "PROD" if environment == "QA" else "QA"
                self.assertNotIn(f"PROMOTION_{other}", sql)

    def test_task_files_never_enable_schedules(self):
        sql = driver.render("pipelines/ops/task.sql", "QA")
        self.assertNotIn(" RESUME", sql)
        self.assertIn("REFRESH_ORDERS(FALSE)", sql)

    def test_v2_changes_only_procedure_and_expectations(self):
        paths = sorted(str(path.relative_to(ROOT / "examples/v2"))
                       for path in (ROOT / "examples/v2").rglob("*") if path.is_file())
        self.assertEqual(paths, ["pipelines/silver/orders.sql", "release.json"])
        self.assertIn(json.loads((ROOT / "release.json").read_text())["version"], ("v1", "v2"))

    def test_setup_has_two_environments_and_no_unresolved_templates(self):
        sql = setup.setup_sql("EXAMPLE-DEMO", "repo:example/demo:environment:qa",
                              "repo:example/demo:environment:prod")
        self.assertNotIn("{{", sql)
        self.assertNotIn("PROMOTION_DEV", sql)
        self.assertIn("'O200', 'cancelled', 50.00", sql)
        self.assertIn("DEFAULT_SECONDARY_ROLES = ()", sql)
        for environment in ("QA", "PROD"):
            self.assertIn(f"CREATE DATABASE PROMOTION_{environment};", sql)
            self.assertIn(f"CREATE USER PROMOTION_CI_{environment}", sql)

    def test_setup_rejects_untrusted_identifiers(self):
        for account, subject in (("bad'account", "repo:example/demo:environment:qa"),
                                  ("EXAMPLE-DEMO", "repo:x/y:environment:qa'; DROP")):
            with self.assertRaises(ValueError):
                setup.setup_sql(account, subject, "repo:example/demo:environment:prod")

    def test_setup_manual_account_check_precedes_provisioning(self):
        sql = setup.setup_sql("EXAMPLE-DEMO", "repo:example/demo:environment:qa",
                              "repo:example/demo:environment:prod")
        self.assertIn("'EXAMPLE-DEMO' AS EXPECTED_ACCOUNT", sql)
        self.assertIn("AS ACTUAL_ACCOUNT", sql)
        self.assertLess(sql.index("AS ACTUAL_ACCOUNT"), sql.index("USE ROLE"))
        self.assertIn("does not automatically block later SQL", sql)
        self.assertNotIn("wrong_account", sql)
        self.assertNotIn("EXECUTE IMMEDIATE", sql)

    def test_setup_sections_keep_raw_seed_separate_from_releases(self):
        sql = setup.setup_sql("EXAMPLE-DEMO", "repo:example/demo:environment:qa",
                              "repo:example/demo:environment:prod")
        self.assertEqual(re.findall(r"^-- (\d{2}) -", sql, re.M),
                         [f"{number:02d}" for number in range(9)])
        self.assertEqual(sql.count("INSERT INTO PROMOTION_SOURCE.RAW.ORDERS"), 1)
        self.assertLess(sql.index("-- 06 -"), sql.index("INSERT INTO"))
        self.assertLess(sql.index("INSERT INTO"), sql.index("-- 07 -"))
        self.assertIn("Promote SQL definitions, not QA data", sql)
        self.assertNotIn("CREATE OR REPLACE PROCEDURE", sql)
        self.assertNotIn("CREATE TASK", sql)
        self.assertNotRegex(sql, r"(?m)^\s*EXECUTE TASK\b")

    def test_local_setup_provisioning_matches_template_when_present(self):
        path = ROOT / "local/setup.sql"
        if not path.exists():
            self.skipTest("optional ignored local setup is absent")
        local = path.read_text()
        subjects = re.findall(r"SUBJECT = '([^']+)'", local)
        rendered = setup.setup_sql("EXAMPLE-DEMO", *subjects)
        body = local[local.index("USE ROLE ACCOUNTADMIN;"):]
        rendered = rendered[rendered.index("USE ROLE ACCOUNTADMIN;"):]
        # Local setup may omit the display-only account check and blank lines.
        normalize = lambda text: [line for line in text.splitlines() if line.strip()]
        self.assertTrue(normalize(body) == normalize(rendered),
                        "local setup differs from its template (values withheld)")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.output = patch("builtins.print")
        self.output.start()
        self.addCleanup(self.output.stop)

    def test_qa_runs_rollback_test_and_never_resumes(self):
        cli = FakeCLI()
        driver.deploy(cli, "QA", COMMIT, sleep=lambda _: None, attempts=2)
        self.assertEqual(cli.calls[0], "PREFLIGHT")
        self.assertTrue(any("REFRESH_ORDERS(TRUE)" in sql for sql in cli.calls))
        self.assertFalse(any(sql.endswith(" RESUME") for sql in cli.calls))
        self.assertIn("RELEASE_LOG", cli.calls[-1])

    def test_prod_logs_after_checks_and_resumes_last(self):
        cli = FakeCLI()
        driver.deploy(cli, "PROD", COMMIT, sleep=lambda _: None, attempts=2)
        self.assertTrue(cli.calls[-1].endswith(" RESUME"))
        self.assertIn("RELEASE_LOG", cli.calls[-2])
        self.assertFalse(any("REFRESH_ORDERS(TRUE)" in sql for sql in cli.calls))

    def test_failures_never_resume(self):
        for failure in ("context", "busy", "deploy", "task", "timeout", "checks", "log"):
            with self.subTest(failure=failure):
                cli = FakeCLI(failure)
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    driver.deploy(cli, "PROD", COMMIT, sleep=lambda _: None, attempts=2)
                self.assertFalse(any(sql.endswith(" RESUME") for sql in cli.calls))

    def test_qa_requires_expected_error_and_unchanged_snapshot(self):
        for failure in ("missing_exception", "wrong_exception", "rollback"):
            cli = FakeCLI(failure)
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                driver.deploy(cli, "QA", COMMIT, sleep=lambda _: None, attempts=2)
            self.assertFalse(any("INSERT INTO PROMOTION_QA.OPS.RELEASE_LOG" in sql for sql in cli.calls))

    def test_resume_failure_is_not_reported_as_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            driver.deploy(FakeCLI("resume"), "PROD", COMMIT, sleep=lambda _: None, attempts=2)

    def test_input_validation(self):
        for environment, commit in (("DEV", COMMIT), ("QA", "main"), ("QA", "A" * 40),
                                     ("PROD", COMMIT + "';")):
            with self.assertRaises(ValueError):
                driver.validate_inputs(environment, commit)

    def test_connection_configuration_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                driver.SnowCLI("QA")

    def test_wrong_account_preflight_fails_before_writes(self):
        environment = {"SNOWFLAKE_ACCOUNT": "EXAMPLE-DEMO", "EXPECTED_ACCOUNT": "EXAMPLE-DEMO",
                       "SNOWFLAKE_USER": "PROMOTION_CI_QA", "SNOWFLAKE_ROLE": "PROMOTION_DEPLOY_QA"}
        with patch.dict(os.environ, environment, clear=True):
            cli = driver.SnowCLI("QA")
            with patch.object(cli, "run", return_value=[{"ACCOUNT": "OTHER-DEMO"}]) as run:
                with self.assertRaises(ValueError):
                    cli.preflight()
                self.assertTrue(run.call_args.args[0].startswith("SELECT"))

    def test_cli_uses_explicit_context_and_parses_json(self):
        environment = {"SNOWFLAKE_ACCOUNT": "EXAMPLE-DEMO", "EXPECTED_ACCOUNT": "EXAMPLE-DEMO",
                       "SNOWFLAKE_USER": "PROMOTION_CI_QA", "SNOWFLAKE_ROLE": "PROMOTION_DEPLOY_QA"}
        with patch.dict(os.environ, environment, clear=True):
            cli = driver.SnowCLI("QA")
            result = subprocess.CompletedProcess([], 0, '[{"TOTAL": 175.25}]', '')
            with patch.object(subprocess, "run", return_value=result) as run:
                self.assertEqual(cli.run("SELECT 175.25 AS TOTAL", rows=True), [{"TOTAL": Decimal("175.25")}])
                command = run.call_args.args[0]
                self.assertIn("-x", command)
                self.assertIn("--enhanced-exit-codes", command)
                self.assertEqual(command[command.index("--role") + 1], "PROMOTION_DEPLOY_QA")
                self.assertNotIn("shell", run.call_args.kwargs)
            for output in ('not json', '{"TOTAL": 1}', '[1]'):
                with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, '')):
                    with self.assertRaises(ValueError):
                        cli.run("SELECT 1", rows=True)

    def test_execute_rejects_wrong_commit_and_dirty_checkout(self):
        for head, status in (("b" * 40, ""), (COMMIT, "?? extra.sql")):
            with patch("sys.argv", ["deploy.py", "QA", COMMIT, "--execute"]):
                with patch.object(subprocess, "check_output", side_effect=[head, status]):
                    with patch.object(driver, "SnowCLI") as snow:
                        self.assertEqual(driver.main(), 1)
                        snow.assert_not_called()

    def test_history_filters_database_schema_and_time(self):
        sql = driver.history_sql("PROMOTION_QA", 1234)
        self.assertIn("DATABASE_NAME => 'PROMOTION_QA'", sql)
        self.assertIn("SCHEMA_NAME => 'OPS'", sql)
        self.assertIn("TO_TIMESTAMP_LTZ(1234, 3)", sql)

    def test_preview_cannot_call_snow_or_git(self):
        with patch("sys.argv", ["deploy.py", "QA", COMMIT]), patch.object(driver, "SnowCLI") as snow:
            with patch("builtins.print"), patch.object(subprocess, "check_output") as git:
                self.assertEqual(driver.main(), 0)
            snow.assert_not_called()
            git.assert_not_called()

    def test_promoted_v2_runs_the_same_driver_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (*driver.PIPELINE_FILES, "checks/results.sql", "fixtures/orders.json", "release.json"):
                source = ROOT / "examples/v2" / relative
                if not source.is_file():
                    source = ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text())
            with patch.object(driver, "ROOT", root), patch("builtins.print"):
                for environment in ("QA", "PROD"):
                    cli = FakeCLI()
                    driver.deploy(cli, environment, COMMIT, sleep=lambda _: None, attempts=2)
                    self.assertTrue(any("'v2'" in sql and "RELEASE_LOG" in sql for sql in cli.calls))

    def test_success_logs_are_suppressed_by_offline_test_harness(self):
        with patch("builtins.print") as output:
            driver.deploy(FakeCLI(), "QA", COMMIT, sleep=lambda _: None, attempts=2)
            self.assertIn("PASS: QA", output.call_args.args[0])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        cls.jobs = cls.workflow["jobs"]

    def test_prod_requires_qa_and_only_main_release_events(self):
        self.assertEqual(self.jobs["prod"]["needs"], "qa")
        self.assertIn("github.ref == 'refs/heads/main'", self.jobs["prod"]["if"])
        self.assertNotIn("pull_request", self.jobs["prod"]["if"])
        self.assertEqual(self.jobs["prod"]["env"]["DEPLOY_SHA"], "${{ github.sha }}")

    def test_deployment_disabled_without_explicit_variable(self):
        for name in ("qa", "prod"):
            self.assertIn("vars.DEPLOY_ENABLED == 'true'", self.jobs[name]["if"])
            self.assertEqual(self.jobs[name]["environment"], name)

    def test_untrusted_forks_never_receive_qa_identity(self):
        self.assertIn("head.repo.full_name == github.repository", self.jobs["qa"]["if"])
        self.assertNotIn("pull_request_target", (ROOT / ".github/workflows/deploy.yml").read_text())

    def test_required_status_rejects_skipped_qa(self):
        job = self.jobs["qa-verified"]
        self.assertEqual(job["needs"], ["checks", "qa"])
        self.assertIn('test "$QA_RESULT" = success', job["steps"][0]["run"])

    def test_actions_pinned_and_no_inline_shell_expressions(self):
        for job in self.jobs.values():
            for step in job["steps"]:
                if "uses" in step:
                    self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")
                self.assertNotIn("${{", step.get("run", ""))

    def test_shared_qa_is_serialized_and_prod_checks_current_main(self):
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])
        steps = self.jobs["prod"]["steps"]
        self.assertIn("commits/main", steps[-2]["run"])
        self.assertIn("--execute", steps[-1]["run"])


if __name__ == "__main__":
    unittest.main()