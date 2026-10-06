# SQL promotion from QA to production

Version a SQL procedure, scheduled task, and reporting view in Git, test a change
in QA, and release the reviewed definitions to PROD through GitHub Actions.

The demo starts **after ingestion**: orders are already in RAW. A task calls a
procedure that transforms those orders into clean tables and reporting output.
QA and PROD are separate databases in one demo account. **Promote code, not QA
data**: each environment processes the shared RAW input independently.

This is an educational demo using invented data. Offline tests are available;
end-to-end Snowflake deployment has not yet been validated.

## 1. What the demo does

```text
PROMOTION_SOURCE.RAW.ORDERS          Already-ingested input (simulated by a seed)
          |
          +--> PROMOTION_QA         Test proposed SQL definitions
          |
          +--> PROMOTION_PROD       Run approved SQL definitions

Inside each environment:
OPS.REFRESH_ORDERS_TASK
          |
          v
SILVER.REFRESH_ORDERS                Procedure with a multi-statement transaction
          |
          +--> SILVER.ORDERS
          +--> GOLD.SALES_SUMMARY --> GOLD.SALES_REPORT
```

The procedure refreshes both output tables, with an exception handler that rolls
back and re-raises errors. The task definition calls it with the failure switch
disabled. The demo's configured schedule is daily at 06:00 UTC; the deployment
driver leaves QA suspended and resumes PROD only after validation.

The change to test is small and visible:

| Release | Business rule | Expected orders | Expected total |
|---|---|---:|---:|
| v1 | Include all orders | 3 | 175.00 |
| v2 | Exclude cancelled orders | 2 | 125.00 |

RAW contains two completed orders worth 100.00 and 25.00, and one cancelled order
worth 50.00. The v2 change affects the procedure and its expected results, not RAW.

## 2. Repository structure

```text
.github/workflows/deploy.yml        Checks, QA deployment, and PROD release jobs
setup/
  setup.sql.j2                      Generic, numbered one-time setup template
  render.py                         Fill setup placeholders locally
fixtures/orders.json               Synthetic RAW input
pipelines/
  tables.sql                       Output tables and release log
  silver/orders.sql                Transactional refresh procedure
  gold/sales_report.sql             Reporting view and reader grant
  ops/task.sql                     Cron-scheduled procedure call
checks/results.sql                 Rows used by deployment validation
release.json                       Expected version, order IDs, count, and total
examples/v2/                       Replacement procedure and manifest for v2
ci/deploy.sh                        Entry point: preview or execute a deployment
ci/deploy.py                        Rendering, deployment, validation, release log
sql/inspect.sql                    Compare QA/PROD outputs and inspect task history
sql/stop_schedule.sql              Suspend the demo's production task
tests/test_repo.py                 Offline tests with mocked Snowflake calls
docs/adopt-existing.md             Bring existing SQL objects into version control
docs/validation.md                 Detailed validation notes and live-test checklist
local/setup.sql                    Your filled setup, if saved; ignored by Git
```

The renderer replaces `{{ database }}` and `{{ environment }}` with the selected
demo target. GitHub Actions checks out a commit and submits its rendered SQL
through Snowflake CLI; this implementation does not use a Snowflake Git clone.

## 3. Local preparation

Use Python 3.11, Git, and a checkout of your own GitHub repository containing this
demo. Run these commands from its root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
bash -n ci/deploy.sh
```

Preview the QA definitions without connecting to Snowflake:

```bash
bash ci/deploy.sh QA aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

The repeated `a` is a dummy commit ID for preview only. The driver does not write
unless `--execute` is supplied. GitHub deployment jobs supply that flag and require
a clean checkout of the requested commit.

## 4. Configure GitHub environments and Snowflake

You need repository administration access, an administrator for a disposable
Snowflake demo account, and an approved way for the Actions runner to connect.
The workflow installs Snowflake CLI on its runner; it is not needed for local
rendering or offline tests.

### Create the GitHub environments first

In **Settings > Environments**, create:

- **`qa`**: allow `main` and the intended pull-request deployment refs. Only trusted
  same-repository contributors should have access to this deployment path.
- **`prod`**: restrict deployments to `main`, configure a required reviewer, and
  review administrator bypass settings. Use a separate reviewer if self-review is
  disabled. Do not enable deployment without the intended approval protection.

Naming an environment in YAML is not the same as configuring its protection rules.
Check feature availability for your repository in the
[GitHub environment setup guide](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

### Prepare the OIDC identities and setup file

The workflow uses OpenID Connect (OIDC), with a separate service user for each
environment. Snowflake's action documentation states: "The SUBJECT must match the
claim GitHub emits for the workflow." Obtain the exact `qa` and `prod` subject
values for **your repository**, including any subject customization; do not assume
the illustrative values below match your repository. Never print or commit tokens.
See the [OIDC setup reference](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cicd/github-action).

Render setup after replacing these example values:

```bash
python setup/render.py \
  --expected-account EXAMPLE-DEMO \
  --qa-subject 'repo:example/sql-promotion-demo:environment:qa' \
  --prod-subject 'repo:example/sql-promotion-demo:environment:prod'
```

The command prints SQL only. Save the result as `local/setup.sql`, which is ignored
by Git. Do not commit filled account configuration.

Open the rendered SQL in an administrator session connected explicitly to the
intended demo account:

1. Run section **00** separately. Compare `ACTUAL_ACCOUNT` and `EXPECTED_ACCOUNT`;
   this is a manual confirmation, not an automatic stop.
2. Review and run sections **01-05** to provision the roles, warehouse, databases,
   schemas, RAW table, grants, and workflow users. Setup uses `ACCOUNTADMIN`;
   workflow jobs use `PROMOTION_DEPLOY_QA` or `PROMOTION_DEPLOY_PROD`.
3. Run section **06** once to seed the synthetic RAW orders, then **07** to inspect
   them. This seed represents ingestion; it is not an ingestion connector.
4. Follow section **08** into the release walkthrough below. Setup does not deploy
   the procedure, view, or task.

Setup is one-time-only. Stop on errors, inspect any partially created objects, and
do not blindly replay it or insert the seed twice.

### Configure repository variables and Actions

In **Settings > Secrets and variables > Actions > Variables**, add:

| Repository variable | Value |
|---|---|
| `SNOWFLAKE_ACCOUNT` | Intended demo account connection identifier |
| `EXPECTED_ACCOUNT` | Exact uppercase `ORGANIZATION-ACCOUNT` shown by setup |
| `DEPLOY_ENABLED` | `false` initially; `true` when ready for the first live run |

Use repository-level variables for this walkthrough. The workflow already defines
the service-user and role names, `id-token: write`, and `use-oidc: true`.
It requests Snowflake CLI 3.17.1 and pins the action revisions. Ensure repository
Actions policy permits those actions. No password or private-key secret is used
by this OIDC configuration.

Snowflake describes the action behavior as: "When OIDC authentication is enabled,
obtains a GitHub-issued OIDC token and sets the Snowflake workload identity
environment variables for subsequent steps."
[Source](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cicd/github-action).

## 5. Run the demo: baseline, change, and promote

### Release v1

Confirm `main` contains the v1 procedure and `release.json`. Once setup, identities,
runner access, and production approvals are ready, set `DEPLOY_ENABLED=true`.
In **Actions > SQL promotion > Run workflow**, select **`main`**.

The run performs offline checks, deploys and validates QA, then waits for the
configured production approval. Review QA before approving. After PROD succeeds,
run `sql/inspect.sql` using an authorized demo session. Expect both reporting
views to show **v1, 3 orders, 175.00**. Treat this first run as live validation,
not as something established by the offline tests.

### Test the v2 change in QA

Create a feature branch and replace these two working files with their counterparts
under `examples/v2/`:

- `pipelines/silver/orders.sql`
- `release.json`

The procedure adds a filter excluding cancelled orders and changes its version
labels to `v2`; the manifest expects `O100` and `O300`, totaling 125.00.
Run the offline tests again, review the diff, commit only the intended changes,
push the branch, and open a pull request to `main` in the same repository.

The PR runs `checks`, `qa`, and `qa-verified`. After successful QA validation,
`sql/inspect.sql` should show **QA v2 / 125.00** and **PROD v1 / 175.00**.
Fork PRs do not deploy. `qa-verified` deliberately fails if QA was skipped, including
when deployment is disabled; an offline-only run is not a QA release approval.

After these status checks have appeared, protect `main` with required PR review
and required `checks` and `qa-verified` statuses, including an up-to-date branch.

### Promote to PROD

Review and merge the PR. The main-branch workflow validates the merged commit in
QA again, then requests production approval. Approve only after inspecting that
run. PROD uses the same merged commit; a pre-deployment check rejects it if `main`
has moved ahead.

Inspect again: both environments should now show **v2, 2 orders, 125.00**.
`OPS.RELEASE_LOG` records the validated commit, rules version, time, and deploying
user. Finish or reject pending runs before starting another: this demo serializes
the whole workflow, including approval waits, because QA is shared.

### What gets tested

- **Offline:** template rendering, fixture business rules, workflow guards, and
  mocked deployment success/failure paths. These do not validate live privileges,
  Snowflake compilation, or transaction behavior.
- **During deployment:** exact order IDs, amounts, counts, total, rules version,
  and a common run token after a successful task run.
- **QA rollback test:** deliberately fail the procedure between output writes;
  require the expected error and unchanged output rows and run tokens.

The driver polls task history rather than treating submission as completion.
Snowflake documents `EXECUTE TASK` as: "Manually triggers an asynchronous single
run of a task" ([reference](https://docs.snowflake.com/en/sql-reference/sql/execute-task)).

After the demo, run the reviewed `sql/stop_schedule.sql` against the intended demo
account and set `DEPLOY_ENABLED=false`. The variable alone does not stop an already
enabled task schedule. On release failure, inspect the job output and task history
before retrying; the driver does not automatically undo deployed definitions.

## 6. What this could look like in production

Keep the same pattern: **versioned definitions -> QA tests -> reviewed release ->
production verification**. Before adapting this demo, plan the following changes:

- **Data separation:** replace the shared fixture with approved QA test data and a
  production ingestion source. Parameterize source locations as well as targets.
- **Access:** replace the broad administrator setup with an approved provisioning
  process; scope each deployment identity to its environment and review runner
  network access. Keep human reporting access separate from CI access.
- **Migrations:** add reviewed schema-change scripts, dependency ordering, and
  compatibility checks. This driver's table file only initializes a fixed schema.
- **Workload design:** replace the fixture-specific full refresh and exact totals
  with a processing strategy and data-quality checks suited to real volume.
- **Release controls:** protect workflow changes, use independent approval, isolate
  concurrent QA changes, and define how releases are queued and identified.
- **Operations and recovery:** add failure alerts, ownership, cost limits, durable
  deployment records, and tested roll-forward and data-recovery procedures.

Do not confuse the procedure's data transaction with rollback of a whole release.
Snowflake states: "Each DDL statement executes as a separate transaction."
Therefore, the procedure's rollback handler does not undo earlier object-definition
changes made by the deployment driver.
[Transactions reference](https://docs.snowflake.com/en/sql-reference/transactions).

For objects originally created outside Git, start with
[Adopting existing objects](docs/adopt-existing.md). For the remaining live checks,
see [Validation notes](docs/validation.md).

## 7. Documentation resources

- [Snowflake CLI GitHub Action](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cicd/github-action): action inputs, OIDC, service users, and subject matching.
- [CI/CD with Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/cicd/integrate-ci-cd): deployment stages and identity recommendations.
- [Transactions](https://docs.snowflake.com/en/sql-reference/transactions): procedure transactions, error handling, and DDL boundaries.
- [EXECUTE TASK](https://docs.snowflake.com/en/sql-reference/sql/execute-task): manual test runs and asynchronous execution.
- [ALTER TASK](https://docs.snowflake.com/en/sql-reference/sql/alter-task): changing task definitions and schedules.
- [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments): reviewers, branch restrictions, and protection settings.