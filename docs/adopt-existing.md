# Adopt an existing SQL definition

Run this exercise only after the synthetic demo has been deployed, using an
authorized role. It does not connect to or export any real application objects.

The `GET_DDL` reference says: "Returns a DDL statement that can be used to recreate
the specified object." It lists procedures, tasks, and views. DDL is the SQL used
to define objects. Source: [GET_DDL](https://docs.snowflake.com/en/sql-reference/functions/get_ddl).

```sql
SELECT GET_DDL('PROCEDURE', 'PROMOTION_QA.SILVER.REFRESH_ORDERS(BOOLEAN)');
SELECT GET_DDL('TASK', 'PROMOTION_QA.OPS.REFRESH_ORDERS_TASK');
SELECT GET_DDL('VIEW', 'PROMOTION_QA.GOLD.SALES_REPORT');
```

`BOOLEAN` identifies the procedure argument type; the reference requires argument
types for procedures with arguments. Review the returned text beside the matching
repository file. These queries are prepared, not executed as part of local validation.

Do not treat the output as a complete release package:

1. Review source and target object references. Replace only the intended environment
   database with the renderer's `{{ database }}` marker. Keep the demo's shared source
   reference deliberate. Do not blindly replace all database names.
2. Inventory dependencies, ownership, grants, schedule settings, and test expectations.
3. Preserve required governance controls when adapting real objects. Do not remove a
   policy merely to make extracted SQL runnable.
4. Establish a reviewed baseline in Git. Make subsequent edits to that baseline and
   validate them in QA before production approval.

The reference warns that generated definitions can differ from the original text:
"For UDFs and stored procedures, the output might be slightly different from the
original DDL." UDF means user-defined function. It also says view output
"Excludes the COPY GRANTS view parameter, even if the original CREATE VIEW
statement specifies the COPY GRANTS parameter."

This example consequently keeps the reader grant explicit in the view file and
does not claim that exporting a definition also exports a full security configuration.