# Promote SQL from QA to production

Change a SQL business rule in Git, test it in QA, and release it to production
with GitHub Actions and a reviewer approval.

```text
RAW.ORDERS ──► QA database    (test the change)
           └─► PROD database  (release after approval)

In each database:  task ──► procedure ──► SILVER.ORDERS ──► GOLD.SALES_REPORT
```

Both databases read the same synthetic RAW orders. Only the SQL is promoted, never data.

| Release | Rule | Orders | Total |
|---|---|---:|---:|
| v1 | Include every order | 3 | 175.00 |
| v2 | Exclude cancelled orders | 2 | 125.00 |

## Repository layout

```text
setup/              One-time account setup (admin)
fixtures/           Three synthetic orders
pipelines/          SQL promoted QA → PROD: tables, procedure, view, task
release.json        Expected result of this release
checks/             Query compared against release.json
examples/v2/        The change you make during the demo
ci/                 Deploy driver (render, deploy, validate)
.github/workflows/  checks → qa → approval → prod
sql/                inspect.sql and stop_schedule.sql, run by hand
tests/              Offline tests
```

## Setup (once)

This walkthrough assumes the demo account has already been prepared using
`setup/`. Account provisioning is separate from the release walkthrough below;
no local Python setup or SQL rendering is included here.

**1. Create the GitHub environments.** In **Settings > Environments**:

- `qa`: no rules needed.
- `prod`: add a required reviewer, and limit it to the `main` branch.

**2. Add repository settings.** In **Settings > Secrets and variables > Actions**:

| Tab | Name | Value |
|---|---|---|
| Secrets | `SNOWFLAKE_ACCOUNT` | `myorg-myaccount` |
| Variables | `DEPLOY_ENABLED` | `true` |

## Demo walkthrough

**Step 1: Release v1.**
In **Actions > SQL promotion > Run workflow**, run it on `main`. QA deploys
and passes its checks, then the run waits for you. Approve `prod`.

**Step 2: Show the baseline.** Run `sql/inspect.sql`:

```text
ENVIRONMENT  ORDER_COUNT  TOTAL_AMOUNT  RULES_VERSION
QA           3            175.00        v1
PROD         3            175.00        v1
```

**Step 3: Make the change.** On a new branch, copy in the v2 files and open a pull request:

```bash
git checkout -b exclude-cancelled
cp examples/v2/pipelines/silver/orders.sql pipelines/silver/orders.sql
cp examples/v2/release.json release.json
git commit -am "Exclude cancelled orders" && git push -u origin exclude-cancelled
```

**Step 4: Show QA vs. PROD.** The pull request deploys to QA only. Once
`qa-verified` is green, run `sql/inspect.sql`:

```text
ENVIRONMENT  ORDER_COUNT  TOTAL_AMOUNT  RULES_VERSION
QA           2            125.00        v2     ← change under test
PROD         3            175.00        v1     ← untouched
```

**Step 5: Promote.** Merge the pull request. The workflow re-tests the merged
commit in QA, then waits for approval. Approve `prod`.

**Step 6: Show the result.** Run `sql/inspect.sql` again. Both environments
show **2 orders, 125.00, v2**. `OPS.RELEASE_LOG` records the commit and the person
who deployed it.

**Step 7: Clean up.** Run `sql/stop_schedule.sql` to suspend the daily
production task.

To repeat the demo, revert the two v2 files on `main`.

## What the pipeline guarantees

- **Nothing reaches PROD without passing QA.** Production deploys only the
  same commit that QA just validated.
- **A person approves every production release.** The `prod` environment
  waits for its reviewer.
- **A stale release is rejected.** If `main` moved after approval, production
  stops before writing anything.
- **Results are checked, not assumed.** After each deploy, the driver runs the
  task and compares the order IDs, count, and total with `release.json`.
- **A failed refresh changes nothing.** In QA, the driver forces the procedure
  to fail midway and confirms its transaction rolled back.

## Optional enhancements

These aren't needed for the demo.

- **Branch ruleset on `main`.** Require a pull request and the `checks` and
  `qa-verified` statuses, so the merge button stays disabled until QA passes.
  Add it after the first pull request, once those checks exist.
- **[Terraform](https://registry.terraform.io/providers/snowflakedb/snowflake/latest/docs)**
  can replace `setup/` and manage roles, warehouses, databases, users, and
  grants as reviewable, repeatable code.
- **[Snowflake DCM Projects](https://docs.snowflake.com/en/user-guide/dcm-projects/dcm-projects-use)**
  (preview) can replace most of `ci/`. Define QA and PROD as targets in
  `manifest.yml`, run `snow dcm plan` on the pull request to show the change
  set, and run `snow dcm deploy` on release. Keep `release.json` as the
  post-deploy check.

A typical split: Terraform for the account foundation, DCM Projects for the
pipeline objects, and GitHub Actions running both behind the same QA test and
approval.
