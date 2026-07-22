# PostgreSQL performance, audit integrity, and recovery runbook

## Implemented safeguards

- High-volume tenant filters have composite indexes for completed sales by time,
  received purchases by time, receipt chronology, stock history, and active
  membership resolution. Deploy these migrations normally; PostgreSQL will take
  brief table locks while creating them, so use a low-traffic release window for
  large existing tables.
- Database connections are health-checked and reusable. Connect, statement, and
  lock timeouts are environment-controlled. Leave statement/lock timeouts at zero
  until production query latency is measured; a starting alert threshold is more
  useful than an arbitrary forced cancellation.
- New tenant and platform-administration audit events are HMAC-SHA256 chained. A tenant row lock serializes
  writers per tenant while different tenants continue concurrently. Updates,
  deletes, bulk writes, and unsealed inserts are rejected by the model layer. On
  PostgreSQL, a trigger also rejects database-level updates/deletes and unsealed
  inserts. Rows created before the integrity migration remain a readable `legacy`
  prefix and are reported by verification.

Set a long, random `DJANGO_AUDIT_LOG_HMAC_KEY` in the secret manager and back it up
separately from the database. Losing it makes verification impossible. For a key
rotation, place the prior key in `DJANGO_AUDIT_LOG_HMAC_KEY_FALLBACKS` before
changing the primary key, verify the chain, and retain the fallback for at least
the audit retention period. Do not log these values.

Run integrity verification on a schedule and after every restore:

```bash
python manage.py verify_audit_log
python manage.py verify_audit_log --tenant-id 42
```

Alert on a non-zero exit. Also ship database logs and verification results to an
immutable external log destination; a hash chain detects changes but does not
replace off-host evidence.

## Logical backup

Install PostgreSQL client tools with a major version equal to or newer than the
server. Store backups on an encrypted, access-controlled, off-host volume. The
command never prints the database password and creates a custom-format archive plus
a SHA-256 manifest:

```bash
python manage.py verify_audit_log
python manage.py db_backup /secure/offsite/pos_20260720.dump
```

Schedule this outside the web process. Copy both `.dump` and `.dump.json`, monitor
age and size, and apply retention only after a newer backup has passed a restore
drill. `pg_dump` is transactionally consistent but is not point-in-time recovery.

## Restore drill and disaster recovery

Restore into an isolated database first. Never test a restore against production.
Stop application workers and background jobs so no process reconnects during a
real replacement. Confirm the configured target name twice:

```bash
python manage.py db_restore /secure/offsite/pos_20260720.dump \
  --confirm-database pos_saas_restore --yes
python manage.py migrate --noinput
python manage.py check --deploy
python manage.py verify_audit_log
python manage.py test
```

Then reconcile tenant counts, users, the most recent sale/stock movement/audit
timestamps, and a sample of financial totals before directing traffic to the
restored service. Record elapsed time to establish the measured RTO.

For a production RPO below the logical-backup interval, enable PostgreSQL physical
base backups and continuous WAL archiving with tested retention in the database
platform (or use managed-service PITR). Monitor archive failures. Quarterly, restore
the latest base backup plus WAL to a timestamp immediately before a known test
transaction, verify the audit log, and record the achieved RPO/RTO. Logical dumps
remain valuable for portability and object-level recovery.

## Database role and security baseline

Use separate credentials for migrations/ownership, the runtime application, and
backup/monitoring. The runtime role should have connect, schema usage, and only the
table/sequence privileges Django needs; it should not own tables, create extensions,
alter roles, disable triggers, or have `SUPERUSER`, `CREATEDB`, `CREATEROLE`,
`REPLICATION`, or `BYPASSRLS`. The backup role needs read access only. Restrict
`pg_hba.conf`/security groups to application and administration networks, require
TLS with certificate verification where supported, and rotate credentials through
the secret manager.

Tenant isolation is currently enforced by scoped managers plus explicit tenant
predicates. PostgreSQL row-level security would be valuable defense in depth, but
must be introduced only after every request, task, report, migration, and admin path
sets a transaction-local tenant identifier. Enabling RLS prematurely could cause
silent partial reports or lock tenants out, so it is intentionally not part of this
backward-compatible change.

## Ongoing performance operations

Enable `pg_stat_statements` at the database platform and review total time, mean
time, calls, rows, and temporary I/O weekly. Use `EXPLAIN (ANALYZE, BUFFERS)` on a
production-sized clone for the top queries. Monitor connection saturation, lock
waits, dead tuples, autovacuum lag, cache hit ratio, replication/WAL lag, and index
usage. Do not remove an apparently unused index until a full business cycle has
been observed; month-end reporting can differ sharply from daily traffic.

Re-run `ANALYZE` after large restores. Keep autovacuum enabled and tune it per hot
table based on measured churn. If PgBouncer uses transaction pooling, set
`DJANGO_DATABASE_DISABLE_SERVER_SIDE_CURSORS=1`.
