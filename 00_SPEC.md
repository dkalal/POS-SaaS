# POS SaaS — Project Spec

This file is the source of truth. Task prompts reference it instead of repeating context. Update this file as decisions change; keep task prompts short.

## Identity

A standalone Django + PostgreSQL POS SaaS product, inspired by YITH POS for WooCommerce. Built and maintained by a small engineering team.

## Relationship to JS-InternetServices

JS-InternetServices is a separate, existing Django system handling ISP/WiFi customer, service, and invoice billing. POS is a completely separate product/application — no shared codebase, no shared business logic.

- POS owns: products, suppliers, purchases, inventory, stock movements, product sales.
- JS-InternetServices owns: customers, subscriptions, service billing, invoices.
- Integration is API-only, one direction: JS-InternetServices reads product/stock/price data from POS via authenticated REST endpoints.
- POS is always the source of truth for stock. JS-InternetServices never mutates POS stock directly.
- All endpoints are versioned from day one: `/api/v1/...`.

## Target users

Retail shops, wholesalers, electronics stores, ISPs selling devices (routers, cables, vouchers, accessories), WiFi hotspot businesses, SMEs in East Africa.

## Non-negotiable system principles

1. No overengineering — every added complexity needs a stated business justification.
2. Cashiers need minimal training — the selling screen must be fast and obvious.
3. SaaS-first — multi-tenant from the schema up (see Multi-Tenancy below). This is not deferred to Phase 3.
4. Offline/hybrid-aware — schema and stock-movement design must not block future offline sync, even though offline sync itself is Phase 3 (see Offline Readiness below).
5. Maintainable by a small team — explicit code over clever abstractions.
6. POS screen must feel instant.
7. Security — no data leakage across tenants, no unauthorized access, all permission checks server-side.
8. Scales from 1 shop to multi-branch enterprises.

## Stack

Use: Django, Django REST Framework, PostgreSQL, Tailwind CSS, HTMX or minimal JS. Add Redis/Celery only when a concrete need appears — do not pre-install either speculatively.

Avoid: microservices, event buses/Kafka, GraphQL, premature distributed architecture, abstractions without a current, stated use.

## Code quality rules

- Thin views, fat service layer. Business logic never lives in views or templates.
- All writes that touch money or stock go through the service layer, inside `transaction.atomic()`.
- Concurrent stock-affecting operations must take a row lock (`select_for_update()`) on the relevant stock row before reading/writing quantity.
- Every service function with a financial or inventory side effect needs: a success-path test, a concurrent-access test, and a rollback/failure test.
- No hard deletes on records that affect financial or inventory history (suppliers, products, purchases, sales) — use `is_active` / soft-delete flags.
- Meaningful naming, cohesive modules, no duplication.

## Multi-tenancy (decided now, not deferred)

Approach: **shared database, shared schema, tenant foreign key on every tenant-scoped model.**

- A `Tenant` model exists from Phase 1, even though Phase 1 ships with a single tenant in practice.
- Every core model (Product, Category, Supplier, Purchase, PurchaseItem, Stock, StockMovement, Sale, SaleItem, Payment, POSUser-role-assignment) has a non-nullable `tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)`.
- All querysets must be tenant-scoped. Use a custom manager or a base queryset mixin that filters by `tenant_id` from the request context — never rely on developers remembering to filter manually in every view.
- Reasoning: retrofitting tenant isolation onto an existing schema is a costly, error-prone migration. Adding an unused-for-now FK column is cheap. This is the one Phase 1 decision that must not be deferred.

## Offline readiness (architectural posture for Phase 1, not implementation)

- `StockMovement` is **append-only**. Never update or delete a movement row. Reversals/cancellations create a new compensating movement record, never an edit of the original.
- This append-only ledger design is what makes future offline sync and conflict resolution tractable — don't compromise it for convenience now.
- Actual offline sync, conflict resolution, and local caching are Phase 3 — do not implement, just don't violate the append-only constraint.

## Critical business rules (apply to all generated code)

1. Stock can never become negative.
2. Sale completion is atomic: stock check, stock decrement, StockMovement write, and Sale/Payment write succeed or fail together inside one `transaction.atomic()` block.
3. Purchase receiving is atomic, with the same all-or-nothing guarantee.
4. Concurrent sales against the same product must not both succeed if stock is insufficient — enforce with `select_for_update()` on the stock row, checked after the lock is acquired.
5. Receipt is generated only after a sale successfully commits.
6. Cancelling a sale reverses stock via a new compensating StockMovement, never by editing the original sale's movements.
7. Cancelling a received purchase reverses stock the same way.
8. Every stock quantity change must have a corresponding StockMovement row. There is no path that changes `Stock.quantity` without writing a movement.
9. RBAC checks happen server-side in the service layer, not only in views/templates and never only in the UI.
10. Financial and inventory records are never hard-deleted if they have history attached.

## RBAC model

Use Django's built-in groups/permissions system plus a thin custom decorator/mixin for tenant-scoped checks. Do not build a custom permission engine — there's no current justification for that complexity.

Roles: Owner/Admin, Manager, Cashier. (Full permission matrix is defined in `02_rbac_matrix.md` task output — generate and keep it as a living artifact, then implement against it.)

## Testing requirements

Minimum for every service touching money or stock:
- Success path test.
- Concurrent-access test (two simulated requests against the same stock row).
- Rollback/failure test (forced exception mid-transaction; assert no partial state).

## Audit logging scope (Phase 1)

Required for: sale creation, sale cancellation, purchase receiving, purchase cancellation, stock adjustments, user role changes. Each entry: actor, tenant, action, target object, timestamp, before/after state where applicable.

## Phase boundaries

**Phase 1 (build now):** Auth/RBAC, Products, Categories, Suppliers, Purchases, Stock, StockMovement, Sales, Payments, Receipts, basic Dashboard. Tenant FK present on all models per above.

**Phase 2 (architecture must not block, do not build):** Branches, registers, cashier sessions with cash reconciliation, receipt template customization, barcode scanning/printing, product import/export, purchase returns, supplier payment tracking, reorder alerts.

**Phase 3 (architecture must not block, do not build):** Full multi-tenant plan/billing layer on top of the Tenant model, API key management UI, usage limits, advanced analytics, offline/hybrid sync.

## Out of scope for Phase 1 unless explicitly requested

Partial payments, branch-level stock, GraphQL anything, microservice extraction.
