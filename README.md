# SQL promotion from QA to production

A small, synthetic example of versioning SQL definitions and releasing them through
quality assurance (QA) into production (PROD). Continuous integration and delivery
(CI/CD) means automated checks and a controlled release process in this example.

**Status: locally tested scaffold, not live-deployment certified.** No account
deployment or GitHub run has been verified. See [validation](docs/validation.md)
before enabling anything. This is a custom educational deployment driver, not a
general-purpose database migration framework.

All orders and amounts are invented. No real organization, account, or business
process is represented. `PROD` here is a demonstration database in a disposable
demo account, not a real production environment.

## The example

| Order | Status | Amount |
|---|---|---:|
| O100 | completed | 100.00 |
| O200 | cancelled | 50.00 |
| O300 | completed | 25.00 |

Baseline **v1** includes all orders: **3 orders, 175.00**. The proposed **v2**
excludes cancelled orders: **2 orders, 125.00**. These are fixture expectations,
not measured customer results or performance claims.

The repository separates three things:

- **Definition:** the SQL text reviewed and versioned in Git.
- **Deployment:** the driver submits those definitions to the selected environment.
- **Execution:** the task calls the procedure to refresh the demo outputs.

The procedure refreshes `SILVER.ORDERS` and `GOLD.SALES_SUMMARY` inside an explicit
transaction. The reporting view is `GOLD.SALES_REPORT`. The scheduled task is
`OPS.REFRESH_ORDERS_TASK`. `SILVER`, `GOLD`, and `OPS` are this demo's schema names
for clean orders, reporting output, and operational objects.

Snowflake's transaction reference states: "A transaction is a sequence of SQL
statements that are committed or rolled back as a unit." It also states:
"Each DDL statement executes as a separate transaction." DDL means Data Definition
Language, the statements that define objects. **The procedure transaction is not
an all-or-nothing release of the SQL definitions.**
Source: [Transactions](https://docs.snowflake.com/en/sql-reference/transactions).

## Files to open first

- `pipelines/silver/orders.sql`: v1 procedure and its exception handler.
- `pipelines/ops/task.sql`: scheduled procedure call; this file never enables it.
- `pipelines/gold/sales_report.sql`: reporting view and explicit reader grant.
- `release.json`: expected version, order identifiers, count, and total.
- `examples/v2/`: only the changed procedure and expectations.
- `ci/deploy.py`: deployment order, input checks, task verification, and release log.
- `setup/setup.sql.j2`: one-time setup template, rendered by `setup/render.py`.
- `.github/workflows/deploy.yml`: GitHub Actions workflow definition.

The `{{ database }}` and `{{ environment }}` markers are Jinja template variables.
The local renderer substitutes `PROMOTION_QA`/`QA` or `PROMOTION_PROD`/`PROD` before
submission. They are not literal SQL object names. `:run_token` in the procedure
is the SQL binding of its local variable; it is deliberately not a template.

## Start offline

From the repository root, install dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
bash -n ci/deploy.sh
```

Preview rendered definitions without connecting:

```bash
bash ci/deploy.sh QA aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

The repeated `a` value is a dummy commit identifier valid only for offline preview.
The driver defaults to preview. `--execute` is the explicit account-write switch;
execution also requires a clean checkout matching the requested real commit.

The tests use an in-memory mock instead of Snowflake. One test evaluates the
portable order-selection query in SQLite, a local database engine. That does not
validate Snowflake syntax, privileges, scheduling, or rollback semantics.

## Prepare a disposable demo account later

Do not run setup against a customer account or a real production namespace.
Have an administrator review the rendered setup, privileges, authentication, and
network policy first. This example does not modify network or authentication policies.

The setup intentionally uses `ACCOUNTADMIN` for one-time provisioning. It is not a
least-privilege administrator runbook. Runtime identities use the dedicated
`PROMOTION_DEPLOY_QA` and `PROMOTION_DEPLOY_PROD` roles. The setup creates the
demo databases, schemas, source rows, warehouse, roles, and service users.

The setup is **one-shot**, not a rerunnable migration. It uses plain `CREATE`
statements to fail on existing names instead of silently adopting objects.
After a partial setup failure, inspect what exists and finish only the missing
steps; do not blindly replay it. Source rows must be inserted exactly once.

OpenID Connect (OIDC) supplies the workflow identity. The documented action behavior
is: "When OIDC authentication is enabled, obtains a GitHub-issued OIDC token and
sets the Snowflake workload identity environment variables for subsequent steps."
The documentation also states: "When a job sets environment:, GitHub uses the
environment form regardless of the trigger."
Source: [Snowflake CLI GitHub Action](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cicd/github-action).
CLI means command-line interface.

Render setup with an expected account and the **exact subject claim emitted by your
repository** for each environment. These values are placeholders, not credentials:

```bash
python setup/render.py \
  --expected-account EXAMPLE-DEMO \
  --qa-subject 'repo:example/sql-promotion-demo:environment:qa' \
  --prod-subject 'repo:example/sql-promotion-demo:environment:prod'
```

The renderer writes SQL to the terminal only. Inspect it before executing it
through an authorized administrator session. Confirm the account-identity check
passes before continuing. If saving actual configuration, use the ignored `local/`
directory and do not publish it. Do not put tokens in source files or commands.

## Configure GitHub later

Creating or publishing a remote repository is not part of the offline setup.
When intentionally enabling the workflow:

1. Create GitHub environments named `qa` and `prod` before any deployment run.
2. Configure required reviewers on `prod`, restrict its deployment branch to
   `main`, and review bypass settings. Check that your GitHub plan supports those
   protections for the chosen repository visibility. **If required reviewers are
   unavailable, leave deployment disabled.**
3. Allow the intended pull-request refs in `qa`; give repository write access only
   to trusted contributors. Same-repository pull requests execute proposed code
   with the QA identity. Fork pull requests run offline tests only.
4. Set repository variables `SNOWFLAKE_ACCOUNT` (connection identifier) and
   `EXPECTED_ACCOUNT` (uppercase organization-account value returned by the
   setup account check). Do not hardcode real account information into public files.
5. Keep `DEPLOY_ENABLED` unset or `false` until the live checks in
   [validation](docs/validation.md) are authorized and scheduled. Set it to `true`
   only when ready to allow account writes.
6. After the first QA test pull request, protect `main`: require a pull request,
   review, an up-to-date branch, and the `checks` and `qa-verified` status checks.
   `qa-verified` deliberately fails when deployment was skipped.

GitHub states: "A job that references an environment must follow any protection
rules for the environment before running or accessing the environment's secrets."
Merely naming `environment: prod` in this workflow does not configure reviewers.
Source: [Managing environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

The workflow reuses pinned action revisions from the source example and requests
Snowflake CLI 3.17.1. Availability and behavior of those dependencies still need an
actual GitHub run. The workflow renders the checked-out SQL locally; it does not
require a second Git repository connection inside Snowflake.

## Run the demonstration after live validation

1. **Baseline:** release v1 from `main` using the manually started workflow,
   inspect QA, and approve production. Both reporting views should show 175.00.
2. **Show definitions:** open the procedure, task, and view files. Explain that
   the release versions these files, not a copied QA dataset.
3. **Change the rule:** on a new feature branch, copy the two files under
   `examples/v2/` to their corresponding root paths. Review the diff: cancelled
   orders are excluded, version labels change, and expected totals become 125.00.
4. **Propose:** commit the change and open a pull request. Offline checks run,
   then QA deployment and validation run when enabled. QA should show 125.00;
   the previously released production version should still show 175.00.
5. **Release:** merge. The merged commit is validated again in QA. Review that
   run before approving production; production then uses the same commit.
6. **Inspect:** production should show 125.00. `OPS.RELEASE_LOG` records the
   validated commit, rules version, time, and deploying service user. GitHub's
   run and approval records supply the human review context.

Use `sql/inspect.sql` after a successful deployment to compare the environments.
Use [existing-object adoption](docs/adopt-existing.md) to explain how an object
created before Git can become a reviewed SQL file.

### What the driver checks

It validates account/user/role context, suspends the existing task, waits for active
runs to drain, submits files in dependency order, triggers a task run, and checks
exact output rows and totals. QA then calls the same procedure with its demo
failure switch enabled and requires both the expected exception and an unchanged
output snapshot, including run tokens. These assertions are implemented but have
not yet been demonstrated against Snowflake.

The driver records validation before enabling the production schedule. QA stays
suspended. The proposed schedule is daily at 06:00 UTC (Coordinated Universal
Time); this is a demo choice, not a product default.

The docs state: "Manually triggers an asynchronous single run of a task" and
"A suspended root task is run without resuming the task" in the
[EXECUTE TASK reference](https://docs.snowflake.com/en/sql-reference/sql/execute-task).
The driver therefore polls for completion rather than equating submission with success.

### Failure, retry, and reset

- A failed release exits nonzero. It does not undo previously submitted definitions.
  Inspect task history and current objects before retrying; do not enable the schedule
  until the release is understood and verified. A timed-out task may still finish.
- The release log records validation, not guaranteed schedule enablement. A resume
  failure after logging is still a failed driver run.
- For this fixed fixture, rerunning the procedure is intended to reproduce the same
  business rows and total. It generates a new run token. Releasing again adds another
  log entry. It is not a literal no-op.
- Reset by reverting the v2 change in a new reviewed pull request, including both
  the procedure and `release.json`. Expected outputs return to v1/175.00 through
  the same QA and production flow. No automatic data recovery is promised.
- After the demonstration, suspend the production task using the separately reviewed
  command in `sql/stop_schedule.sql`. Turning off GitHub deployments does not execute
  that command. Resource deletion is intentionally not automated.

### Scope and limitations

This is a single-presenter demo with shared QA. All runs, including production
approval waits, share one workflow concurrency group. Finish or reject an outstanding
release before starting another. Do not manually execute tasks or write demo output
tables during validation. A pre-production check rejects a release when `main` has
moved, but this is not a general release queue or a distributed lock.

Table files initialize a fixed schema; the driver does not implement table schema
migrations, drift detection, backups, or automatic rollback. The workload fully
refreshes only its synthetic output tables. Do not apply that refresh approach
unmodified to real data. The shared fixture source is not an ingestion integration.
The reader role is defined but assignment to human users is an administrator decision.

## Before making a repository public

Review every file and staged diff, including hidden files, history, workflow logs,
generated setup, and screenshots. Keep actual account identifiers, identities,
tokens, local paths, and private material out. `.gitignore` is a convenience, not
a privacy guarantee. Decide licensing and organizational publication approval
separately; this scaffold does not grant a license or authorize publication.