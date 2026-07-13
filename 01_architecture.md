# Task: Architecture & App Structure

Read `00_SPEC.md` in this repo before doing anything else — it is the binding source of truth for stack, principles, multi-tenancy, and business rules. Do not restate it back to me; just follow it.

You are a senior Django/PostgreSQL architect with deep POS and multi-tenant SaaS experience.

## Do this, in order, stopping after each step for my confirmation before continuing:

**Step 1 — App structure.** Propose the Django app breakdown (e.g. `tenants`, `accounts`, `catalog`, `suppliers`, `purchasing`, `inventory`, `sales`, `payments`, `api`). For each app: its single responsibility, and which models it owns. Flag any app whose responsibility overlaps another.

**Step 2 — Model list per app.** For each app, list the models with their key fields (not full Django field syntax yet — just name + type + nullable?). Every tenant-scoped model must show the `tenant` FK per spec. Flag any model that's missing an obvious field or that conflates two concerns.

**Step 3 — Service layer map.** For each app with financial or inventory side effects, list the service functions it needs (e.g. `purchasing.services.receive_purchase(purchase_id, user)`), and which critical business rule(s) from the spec each one must enforce.

Do not generate code in this task. Output is the architecture document only. End by listing any open decisions you need me to make before Step 4 (schema design) can start.
