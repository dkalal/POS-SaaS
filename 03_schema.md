# Task: Database Schema (Phase 1 models)

Read `00_SPEC.md` first. Read the architecture output from `01_architecture.md` and RBAC matrix from `02_rbac_matrix.md` if available in this conversation/repo — build on them, don't redesign from scratch.

You are a Django/PostgreSQL database architect.

## Produce, for each Phase 1 app

1. Full Django model code: fields, types, `null`/`blank`, `related_name`, `Meta` (indexes, constraints, ordering).
2. Every tenant-scoped model has `tenant = models.ForeignKey('tenants.Tenant', on_delete=models.PROTECT)` and a `unique_together`/`UniqueConstraint` scoped to `(tenant, ...)` wherever uniqueness is required (e.g. SKU unique per tenant, not globally).
3. `StockMovement` is append-only: no `updated_at`, no edit path. Include a `reference_type` + `reference_id` (or a `GenericForeignKey` if you justify why) linking back to the originating Purchase/Sale/Adjustment.
4. Explicit `CheckConstraint` preventing negative stock at the database level, in addition to the application-level lock — defense in depth per spec rule 1.
5. Indexes on: SKU, barcode, tenant+SKU, tenant+active-status, and any FK used in hot-path queries (sale lookups, stock lookups).
6. A short paragraph per model explaining any deviation from the architecture doc, if any.

End with a list of migrations you'd generate (just the names/order, not the migration files) and call out any migration that needs a data migration step (e.g. backfilling `tenant_id` if this were an existing system — note it even though Phase 1 starts fresh).
