# POS SaaS Admin Runbook

Mwongozo huu ni wa msimamizi/on-call wa production. Unahusu Docker Compose,
PostgreSQL na Redis zilizo managed. Hifadhi maamuzi ya kila tukio kwenye change
au incident record.

> **Kanuni za usalama:** Usichapishe secret, database URL, token, au data ya
> mteja kwenye terminal history, ticket, chat, au log. PostgreSQL/Redis zibaki
> kwenye private network. Restore hubadilisha database; drill ifanywe kwenye
> database iliyotengwa, na production restore lazima iidhinishwe.

## 1. Service contract

| Kipengele | Hali inayotarajiwa | Uthibitisho |
| --- | --- | --- |
| Web | Inaendelea na ipo `127.0.0.1:8000` pekee | `docker compose ... ps` |
| Liveness | Process inaweza kujibu HTTP | `/healthz/` = `200` |
| Readiness | Web, PostgreSQL na Redis ziko tayari | `/readyz/` = `200` |
| Audit log | HMAC chain ni sahihi | `python manage.py verify_audit_log` |

`/healthz/` hupima process pekee. Tumia `/readyz/` kwa load-balancer routing na
acceptance baada ya deployment, kwa kuwa hupima database na cache pia.

## 2. Ownership and recovery objectives

Kabla ya production ya kwanza, andika owner wa on-call, change approver,
database-provider escalation, backup retention, na malengo yafuatayo:

| Kipimo | Maana |
| --- | --- |
| RPO | Kiasi kikubwa cha data kinachokubalika kupotea; mfano, saa 24 kwa backup ya kila siku |
| RTO | Muda mkubwa wa kurejesha huduma; upimwe kwenye restore drill |

Tumia credentials tofauti kwa migration, runtime, na backup. Deployment identity
ipewe read-only access ya `SECRETS_DIR`. Hifadhi
`DJANGO_AUDIT_LOG_HMAC_KEY` kwa usalama na tofauti na database backup; bila key
hii, audit chain haiwezi kuthibitishwa baada ya recovery.

## 3. Production readiness

1. Build na publish image iliyopitiwa kwa immutable digest; weka digest hiyo
   kwenye `POS_SAAS_IMAGE`, si tag inayobadilika kama `latest`.
2. Nakili `.env.production.example` nje ya repository kama `.env.production`.
   Weka hostname kamili, HTTPS public URL, na proxy address sahihi—bila secrets.
3. Tengeneza files zinazorejelewa na `_FILE` variables ndani ya `SECRETS_DIR`.
   Database URL itumie `sslmode=verify-full` na CA iliyomountiwa inapohitajika.
4. Reverse proxy iterminate TLS na iproxy tu kwenda
   `127.0.0.1:${WEB_PORT:-8000}`. Ibadilishe (replace) `X-Forwarded-Proto`, si
   kuongeza tu, kabla ya kuwezesha proxy trust.
5. Alert kwenye health/readiness, container health, database, Redis, backup
   age/size, na audit verification. Tuma logs kwenye external durable storage.
6. Ikiwa RPO ni fupi kuliko logical-backup interval, washa managed PostgreSQL
   PITR/WAL retention. Logical dump peke yake haiwezi point-in-time recovery.

Thibitisha production configuration kabla ya kufungua traffic:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml run --rm web \
  python manage.py check --deploy
```

Rekebisha kila error. `config` command ya Docker inaweza kuonyesha configuration
values; usiambatishe output yake kwenye ticket.

## 4. Deploying a release

### Pre-flight

- Rekodi running image digest, target digest, operator, na approver. Hakikisha
  target imepita CI na previous digest bado ipo kwa rollback.
- Thibitisha `/readyz/` ni `200`, hakuna incident, DB/Redis alerts ziko sawa, na
  backup ya karibuni iko ndani ya RPO.
- Pitia migration impact. Kwa table kubwa, tumia low-traffic window. Migration
  irun mara moja tu kwa kila release, si kwa kila web replica.
- Hakikisha config na secret files zipo/readable bila kuonyesha contents.

### Release procedure

Tumia directory yenye `docker-compose.production.yml`:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml config
docker compose --env-file .env.production -f docker-compose.production.yml pull
docker compose --env-file .env.production -f docker-compose.production.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-deps web
docker compose --env-file .env.production -f docker-compose.production.yml ps
```

Compose hii haina `db` service kimakusudi; application inaunganishwa na managed
PostgreSQL na Redis. Baada ya rollout, subiri container iwe healthy, kisha:

```bash
curl --fail --silent --show-error http://127.0.0.1:${WEB_PORT:-8000}/healthz/
curl --fail --silent --show-error http://127.0.0.1:${WEB_PORT:-8000}/readyz/
```

Kagua kupitia public HTTPS hostname: login page na workflow moja ya read-only
kwa user mwenye ruhusa. Fuata logs/monitoring kwa dakika 5–15, halafu rekodi
digest, migration result, health results, operator, na timestamp. Ratibu
`verify_audit_log` nje ya web process.

### Rollback

Kwa web-only regression, rudisha `POS_SAAS_IMAGE` kwenye previous recorded digest
na run `up -d --no-deps web`; thibitisha health endpoints na affected workflow.

**Usifanye database rollback kwa restore au schema edits wakati wa normal web
rollback.** Migration incompatible/data change inahitaji kusimamisha rollout na
escalation kwa release owner/DBA, pamoja na recovery plan iliyoidhinishwa.

## 5. Backup operations

### Logical backup

Endesha kwenye admin job/container iliyofungwa, yenye PostgreSQL client tools
major version sawa au mpya kuliko server. Hifadhi backup kwenye encrypted,
access-controlled, off-host storage.

```bash
python manage.py verify_audit_log
python manage.py db_backup /secure/offsite/pos_YYYYMMDD_HHMM.dump
```

Command huunda custom-format `.dump` pamoja na SHA-256 manifest
`.dump.json`. Copy zote mbili kwenda off-host storage. Audit verification ikiwa
non-zero, simama na escalate: backup ya audit chain isiyothibitishwa si backup
iliyofanikiwa.

### Success criteria and schedule

- Dump na manifest zipo, zina non-zero/sensible size, na job imefaulu.
- Encryption in transit/at rest, least-privilege access, na monitoring ya backup
  age na size vinathibitishwa.
- Usitumie `--overwrite` kwenye scheduled job; filename ya timestamp ihifadhi
  recovery point ya jana.
- Endesha daily au mara nyingi zaidi kulingana na RPO; restore drill angalau
  quarterly na baada ya DB version, storage, credentials, au automation change.
- Ondoa backup ya zamani baada tu ya backup mpya kufaulu restore drill.

## 6. Restore and disaster recovery

### Restore drill (non-production)

Tengeneza isolated PostgreSQL database, isolated Redis, na temporary environment
inayoiangalia; usiwe na public route. Hakikisha configured target database name
kabla ya command hii destructive:

```bash
python manage.py db_restore /secure/offsite/pos_YYYYMMDD_HHMM.dump \
  --confirm-database pos_saas_restore --yes
python manage.py migrate --noinput
python manage.py check --deploy
python manage.py verify_audit_log
```

Reconcile tenant/user counts, latest sale/stock movement/audit timestamps, na
sample ya financial totals. Run `ANALYZE` baada ya large restore. Rekodi source
backup, checksum, start/end, verification, gaps, achieved RPO, na achieved RTO.

### Production recovery

Anza tu baada ya incident authority kuidhinisha:

1. Declare incident; rekodi symptom, start time, last-known-good release, na
   selected recovery point. Preserve logs/provider events.
2. Funga traffic kwenye proxy, simamisha web containers, jobs, na integrations
   ili zisiandike wakati wa recovery.
3. Prefer provider PITR kwenda new isolated instance kama inatimiza RPO;
   vinginevyo chagua newest verified logical dump.
4. Restore na validate kwanza kwenye isolated target kwa drill procedure.
5. Second person athibitishe counts, financial samples, timestamps, audit check,
   na readiness. Andika data loss dhidi ya RPO.
6. Baada ya approval, elekeza production config/secrets kwenye recovered DB,
   deploy known-good image, na fungua traffic baada ya `/readyz/` na functional
   checks kupita.
7. Fungua traffic taratibu, monitor errors/latency, rotate credentials kama kuna
   suspicion ya exposure, na fanya incident review.

Restore command huthibitisha manifest checksum kwa default na hukataa bila
`--yes` na exact `--confirm-database`. `--skip-checksum` itumike tu kwa incident
approval iliyoandikwa.

## 7. Routine controls

| Frequency | Control | Evidence |
| --- | --- | --- |
| Kila release | `check --deploy`, migration, health/readiness | Change record |
| Daily / per RPO | Audit verification na logical backup | Job result, backup age/size |
| Weekly | Capacity, connections, locks, errors, slow queries | Operations review |
| Quarterly | Isolated restore + PITR drill | Measured RPO/RTO |
| After restore/key rotation | `verify_audit_log` | Command result |

Kwa audit-key rotation: weka old key kwenye
`DJANGO_AUDIT_LOG_HMAC_KEY_FALLBACKS` kabla ya kubadilisha primary key, verify
chain, na retain fallback kwa audit retention period.

## 8. Common issues and first response

| Symptom | Safe first checks | Response / escalation |
| --- | --- | --- |
| `/healthz/` fails | Container status/logs, host disk/memory | Preserve logs; investigate release. Roll back confirmed bad web release. |
| Health works, `/readyz/` = `503` | DB/Redis provider health, DNS/TLS, secret mounts, logs | Remove from routing; repair dependency. Repeated restart haitatibu provider outage. |
| Deploy haianzi | `ps`, container logs, image digest, config validation | Linganisha target/previous config; fix secret/config or rollback web image. |
| Migration fails | Exact error, locks/capacity, current schema | Stop rollout; usirun blindly au kudrop table. Escalate DBA/release owner. |
| Login/session inconsistency | Redis health, cache URL/key prefix, proxy config | Redis ni required production dependency; confirm all instances share it. |
| HTTPS loop/CSRF failures | Public URL, allowed hosts, CSRF origins, header replacement | Rekebisha exact proxy/host/origin config; never allow all. |
| Slow reports/timeouts | DB saturation, locks, slow-query metrics, release changes | Investigate on production-sized clone using `EXPLAIN (ANALYZE, BUFFERS)`. |
| Audit verification fails | Preserve output, last-good check, key/fallback, DB events | Security/data-integrity incident; usibadilishe audit rows/keys kwanza. |
| Backup stale/fails | Storage capacity/permission, client version, TLS/job logs | Escalate before RPO breach; usifute last known-good backup. |
| Restore checksum mismatch | Dump/manifest from off-host storage | Stop, re-copy/reselect. `--skip-checksum` needs explicit approval. |

Soma [POSTGRES_OPERATIONS.md](POSTGRES_OPERATIONS.md) kwa PITR, performance,
database privilege, na audit integrity details; na [SECURITY.md](../SECURITY.md)
kwa security assumptions.

## 9. Incident handover record

```text
Incident/change ID:
Start/end time (UTC):
Operator and approver:
Customer impact / affected tenants:
Current and target image digest:
Database recovery point (if applicable):
Actions and exact command outcomes:
Health/readiness and functional-check outcomes:
Backup/audit verification outcome:
Data-loss estimate against RPO:
Follow-up owner and due date:
```
