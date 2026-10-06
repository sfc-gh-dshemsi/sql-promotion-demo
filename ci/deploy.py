"""Release driver. Rendering is offline; --execute explicitly opts into account writes."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_FILES = (
    "pipelines/tables.sql",
    "pipelines/silver/orders.sql",
    "pipelines/gold/sales_report.sql",
    "pipelines/ops/task.sql",
)


def redact_diagnostic(text):
    """Remove configured secrets and common credential forms from error text."""
    for name, value in sorted(os.environ.items(), key=lambda item: len(item[1]), reverse=True):
        if value and any(marker in name.upper() for marker in
                         ("ACCOUNT", "TOKEN", "PASSWORD", "SECRET", "PRIVATE_KEY", "PASSPHRASE")):
            text = text.replace(value, "[REDACTED]")
            if "ACCOUNT" in name.upper():
                pattern = re.escape(value).replace("_", "[-_]")
                text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  "[REDACTED]", text, flags=re.DOTALL)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                  "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
    # Strip terminal controls, and prevent diagnostic lines becoming workflow commands.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "\n".join("  " + line for line in text.splitlines())


def validate_inputs(environment, commit):
    if environment not in ("QA", "PROD"):
        raise ValueError("environment must be QA or PROD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full lowercase 40-character Git commit ID")


def render(path, environment):
    templates = Environment(loader=FileSystemLoader(str(ROOT)), undefined=StrictUndefined,
                            autoescape=False, keep_trailing_newline=True)
    return templates.get_template(path).render(
        database=f"PROMOTION_{environment}", environment=environment)


def check_results(rows, release):
    """Check exact fixture rows as well as totals; rejects duplicates and missing rows."""
    orders = [row for row in rows if row["ROW_KIND"] == "ORDER"]
    summary = [row for row in rows if row["ROW_KIND"] == "SUMMARY"]
    if len(rows) != len(orders) + len(summary) or len(summary) != 1:
        raise ValueError("expected exactly one summary and only ORDER/SUMMARY rows")
    expected_ids = sorted(release["expected_order_ids"])
    if sorted(row["ORDER_ID"] for row in orders) != expected_ids:
        raise ValueError("order identifiers differ from the release expectations")
    fixtures = {row["order_id"]: row for row in json.loads((ROOT / "fixtures/orders.json").read_text())}
    for row in orders:
        fixture = fixtures[row["ORDER_ID"]]
        if row["STATUS"] != fixture["status"] or Decimal(str(row["AMOUNT"])) != Decimal(fixture["amount"]):
            raise ValueError("order values differ from the synthetic fixture")
    if any(row["RULES_VERSION"] != release["version"] for row in rows):
        raise ValueError("mixed or unexpected rules versions")
    tokens = {row["RUN_TOKEN"] for row in rows}
    if len(tokens) != 1 or not next(iter(tokens)):
        raise ValueError("outputs do not belong to one nonempty run token")
    total = sum((Decimal(str(row["AMOUNT"])) for row in orders), Decimal(0))
    expected_total = Decimal(release["expected_total"])
    if (len(orders) != release["expected_count"] or total != expected_total
            or Decimal(str(summary[0]["TOTAL_AMOUNT"])) != expected_total
            or int(summary[0]["ORDER_COUNT"]) != len(orders)):
        raise ValueError("summary or order totals differ from release expectations")


class SnowCLI:
    def __init__(self, environment):
        self.environment = environment
        self.role = f"PROMOTION_DEPLOY_{environment}"
        self.user = f"PROMOTION_CI_{environment}"
        self.account = os.environ.get("SNOWFLAKE_ACCOUNT", "")
        if not re.fullmatch(r"[A-Za-z0-9]+-[A-Za-z0-9_-]+", self.account):
            raise ValueError("set SNOWFLAKE_ACCOUNT to the intended organization-account identifier")
        for key, expected in (("SNOWFLAKE_ROLE", self.role), ("SNOWFLAKE_USER", self.user)):
            if os.environ.get(key) != expected:
                raise ValueError(f"{key} must equal {expected}")

    def run(self, sql, rows=False):
        command = ["snow", "sql", "-x", "--account", self.account,
                   "--user", self.user, "--role", self.role,
                   "--warehouse", "PROMOTION_WH", "--format", "json",
                   "--enhanced-exit-codes", "-q", sql]
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        if not rows:
            return None
        result = json.loads(completed.stdout, parse_float=Decimal)
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ValueError("unexpected Snowflake CLI JSON result shape")
        return result

    def preflight(self):
        rows = self.run("SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS ACCOUNT, "
                        "CURRENT_ROLE() AS ROLE, CURRENT_USER() AS USER", rows=True)
        # Account-name underscores also have a hyphenated connection form.
        normalize_account = lambda value: value.upper().replace("_", "-")
        if (len(rows) != 1 or not isinstance(rows[0].get("ACCOUNT"), str)
                or normalize_account(rows[0]["ACCOUNT"]) != normalize_account(self.account)
                or rows[0].get("ROLE") != self.role or rows[0].get("USER") != self.user):
            raise ValueError("active account, role, or user differs from the explicit expected context")


def history_sql(database, since=None):
    start = f"TO_TIMESTAMP_LTZ({since}, 3)" if since is not None else "DATEADD('day', -1, CURRENT_TIMESTAMP())"
    return f"""SELECT STATE, QUERY_ID, ERROR_MESSAGE FROM TABLE(
        SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY(
            TASK_NAME => 'REFRESH_ORDERS_TASK', DATABASE_NAME => '{database}',
            SCHEMA_NAME => 'OPS', SCHEDULED_TIME_RANGE_START => {start},
            SCHEDULED_TIME_RANGE_END => CURRENT_TIMESTAMP(), RESULT_LIMIT => 10000))
        ORDER BY SCHEDULED_TIME DESC"""


def deploy(cli, environment, commit, sleep=time.sleep, attempts=36):
    validate_inputs(environment, commit)
    database = f"PROMOTION_{environment}"
    task = f"{database}.OPS.REFRESH_ORDERS_TASK"
    release = json.loads((ROOT / "release.json").read_text())
    if release["version"] not in ("v1", "v2"):
        raise ValueError("unsupported demo release")
    cli.preflight()
    cli.run(f"ALTER TASK IF EXISTS {task} SUSPEND")
    # Suspension does not terminate an already executing run. Wait before replacing code.
    for _ in range(attempts):
        active = cli.run(history_sql(database), rows=True)
        if not any(row["STATE"] in ("EXECUTING", "SCHEDULED") for row in active):
            break
        sleep(5)
    else:
        raise ValueError("existing task has not drained; leave suspended and inspect task history")

    for path in PIPELINE_FILES:
        cli.run(render(path, environment))

    started = cli.run("SELECT DATE_PART(EPOCH_MILLISECOND, CURRENT_TIMESTAMP()) AS STARTED", rows=True)[0]["STARTED"]
    started = int(started)
    cli.run(f"EXECUTE TASK {task}")
    for _ in range(attempts):
        history = cli.run(history_sql(database, started), rows=True)
        if history and history[0]["STATE"] == "SUCCEEDED":
            break
        if history and history[0]["STATE"] not in ("SCHEDULED", "EXECUTING"):
            raise ValueError(f"task did not succeed: {history[0]['STATE']}")
        sleep(5)
    else:
        raise ValueError("task verification timed out; task may still finish; inspect history before retrying")

    result_sql = render("checks/results.sql", environment)
    before = cli.run(result_sql, rows=True)
    check_results(before, release)
    if environment == "QA":
        try:
            cli.run(f"CALL {database}.SILVER.REFRESH_ORDERS(TRUE)")
        except subprocess.CalledProcessError as error:
            if "DEMO_INJECTED_FAILURE" not in (error.stderr or "") + (error.stdout or ""):
                raise
        else:
            raise ValueError("injected failure unexpectedly succeeded")
        after = cli.run(result_sql, rows=True)
        if before != after:
            raise ValueError("rollback test changed output rows or run tokens")

    cli.run(f"INSERT INTO {database}.OPS.RELEASE_LOG (COMMIT_SHA, RULES_VERSION, DEPLOYED_BY) "
            f"SELECT '{commit}', '{release['version']}', CURRENT_USER()")
    if environment == "PROD":
        cli.run(f"ALTER TASK {task} RESUME")
    print(f"PASS: {environment} validated {release['version']} at {commit}; "
          f"schedule {'enabled' if environment == 'PROD' else 'suspended'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=("QA", "PROD"))
    parser.add_argument("commit")
    parser.add_argument("--execute", action="store_true", help="allow writes to the configured account")
    args = parser.parse_args()
    try:
        validate_inputs(args.environment, args.commit)
        if not args.execute:
            for path in PIPELINE_FILES:
                print(f"-- {path}\n{render(path, args.environment)}")
            print("-- OFFLINE PREVIEW ONLY: preflight, execution, checks and release log are not run.")
            return 0
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True).strip()
        if head != args.commit or dirty:
            raise ValueError("execution requires a clean checkout of the requested commit")
        deploy(SnowCLI(args.environment), args.environment, args.commit)
        return 0
    except subprocess.CalledProcessError as error:
        print(f"FAIL: subprocess exited with code {error.returncode}.", file=sys.stderr)
        diagnostic = error.stderr or error.stdout
        if diagnostic:
            print(redact_diagnostic(diagnostic), file=sys.stderr)
        else:
            print("  No diagnostic output was returned.", file=sys.stderr)
        print("No automatic release rollback; inspect state before retrying.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError) as error:
        print(f"FAIL: {error}. No automatic release rollback; inspect state before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())