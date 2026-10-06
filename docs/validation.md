# Validation status

## Local evidence

The final offline run passed **28 tests**; `bash -n ci/deploy.sh` also passed.
Command: `python3 -m unittest discover -s tests -v`. Tests exercise:
template rendering for both environments; v1/v2 fixture totals and row identities;
explicit transaction structure; mocked failure paths; QA rollback-test enforcement;
production schedule-enable ordering; workflow conditions; and input validation.

Tests labeled rollback or task execution use mocks. They are tests of driver
behavior, not evidence of a real Snowflake rollback or task run. The portable SELECT
test uses SQLite and is not a Snowflake compilation check.

No Snowflake SQL was executed or compiled. No GitHub workflow was run. No real
account identifiers or tokens were configured. This status must be updated with
actual evidence before a live demonstration is described as verified.

Dependencies used locally: Jinja2 3.1.6 and PyYAML 6.0.2, matching the requirement
pins. Optional actionlint and shellcheck executables were not installed; YAML
structure tests and `bash -n` do not replace those validators.

Manual local review corrected a release-test defect: the tests initially assumed
the working tree always contained v1. The suite now accepts the selected release
manifest and separately exercises a temporary v2 overlay through the mocked driver.

The content scan found no supplied customer/participant names, source account or
repository identifiers, personal paths, private links, or recognizable credential
patterns in candidate files. URL matches were public documentation and the OIDC
issuer. The fresh repository has no commits or remote. This is a scoped scan,
not a guarantee against every possible disclosure; review future additions too.

## Documentation and review limitations

Current primary documentation was retrieved for transactions, exceptions,
ALTER TASK, EXECUTE TASK, TASK_HISTORY, and the Snowflake CLI GitHub Action.
The documentation search tool then returned search-limit errors. Direct public
documentation reads supplemented SQL CLI, CREATE VIEW, and GET_DDL references;
these are not a substitute for a completed independent claim-verification pass.

An independent verifier was not run in this implementation session. The review is
partial. Setup privileges, every emitted SQL statement, installed CLI compatibility,
and the full customer-facing claim inventory still require independent verification.
Do not infer production readiness from successful local tests.

## Required live checks (not performed)

- Administrator reviews setup in a disposable demo account and confirms names are
  unused. Validate provisioning, exact OIDC subjects, network access, and identity
  grants. Do not execute account setup merely to review this repository.
- Validate both identities: QA cannot write production; production cannot write QA;
  neither can write the shared source; reader roles can read only the intended report.
  Include inherited and PUBLIC privileges in that review.
- Run v1 in QA. Require exactly O100/O200/O300 and total 175.00, then run it again
  and confirm stable business results with a new run token.
- Run the forced failure. Require DEMO_INJECTED_FAILURE and byte-equivalent ordered
  result values before and after, including run tokens. Test a real statement failure
  separately to exercise the generic exception path.
- Confirm the task executes under its intended owner and that history polling selects
  the intended execution. Check both failure and timeout behavior.
- Release v1 to the demo production database. Demonstrate the GitHub reviewer gate
  before deployment and verify schedule enablement only after checks.
- Apply v2 in a pull request. Confirm QA becomes 125.00 while production remains
  175.00. Merge and revalidate the merged commit in QA, then approve production.
- Test a bad expectation, syntax error, disabled deployment variable, skipped QA,
  stale production approval, and attempted fork deployment. Require no production
  release for failed/skipped QA and no schedule enablement on driver failure.
- Verify the actual CLI JSON/error format and pinned action/dependency availability.
- Reset via a reviewed revert of both changed files. Confirm v1/175.00 returns.
- Suspend the demo schedule after testing. Review and approve any object removal
  separately; no cleanup/deletion script is run automatically.

## Documentation receipts

- Transactions: "Explicit transactions should contain only DML statements and query
  statements. DDL statements implicitly commit active transactions".
  [Source](https://docs.snowflake.com/en/sql-reference/transactions)
- Exception propagation: "In these cases, execute the RAISE command without specifying
  any arguments."
  [Source](https://docs.snowflake.com/en/developer-guide/snowflake-scripting/exceptions)
- Task suspension: "When a task is suspended, any current run of the task (i.e. a run
  with an EXECUTING state in the TASK_HISTORY output) is completed."
  [Source](https://docs.snowflake.com/en/sql-reference/sql/alter-task)
- History filtering: "Use function arguments such as DATABASE_NAME, SCHEMA_NAME, and
  TASK_NAME to filter results whenever possible."
  [Source](https://docs.snowflake.com/en/sql-reference/functions/task_history)

These receipts support the specific decisions named, not every claim or SQL token
in the repository. DML means Data Manipulation Language, such as INSERT and DELETE.