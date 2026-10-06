# SQL promotion demo requirements

This is a synthetic educational example, not an account migration framework.

- REQ-01: The repository SHALL contain only generic configuration and invented data, with no inherited history or private source material.
- REQ-02: WHEN baseline v1 runs, the workload SHALL report three orders totaling 175.00.
- REQ-03: WHEN v2 runs, the workload SHALL exclude cancelled orders and report two orders totaling 125.00.
- REQ-04: WHEN the procedure fails between its data changes, its exception handler SHALL roll back its transaction and rethrow the error. Live verification remains required.
- REQ-05: WHEN a release targets QA or PROD, the driver SHALL validate its inputs and active connection context before changing objects.
- REQ-06: WHEN deployment or verification fails, the driver SHALL exit nonzero without enabling the schedule or claiming release rollback.
- REQ-07: WHEN production verification succeeds, the driver SHALL record the validated commit before enabling the scheduled task.
- REQ-08: WHEN a proposed change is merged, the workflow SHALL revalidate the merged commit in QA before offering production deployment of that same commit.
- REQ-09: The workflow SHALL remain disabled for account deployment until explicitly configured. Production approval SHALL require separately configured GitHub environment protection.
- REQ-10: Offline tests SHALL use mocked command execution, never a Snowflake connection.