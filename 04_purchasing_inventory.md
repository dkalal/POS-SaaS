# Task: Purchasing & Stock Inflow Service Layer

Read `00_SPEC.md` first, specifically: critical business rules 1, 3, 6, 8, the multi-tenancy section, and the testing requirements. Build against the models from `03_schema.md`.

## Produce

1. `purchasing/services.py` covering:
   - `create_draft_purchase(tenant, supplier, items, created_by)` — draft purchases do not touch stock.
   - `receive_purchase(purchase_id, received_by)` — atomic: locks the relevant stock rows with `select_for_update()`, increments `Stock.quantity`, writes one `StockMovement` per `PurchaseItem` with type `purchase_in`, updates `Purchase.status` to Received, sets `received_by`/`received_date`. All inside one `transaction.atomic()`.
   - `cancel_received_purchase(purchase_id, cancelled_by)` — atomic: writes compensating `StockMovement` rows (never edits the originals), decrements stock back down, guards against taking stock negative (raise a domain exception if it would).
   - Cost price update policy: implement "cost price may update from latest purchase" as an explicit, named function (`update_cost_price_from_purchase`) called only when a tenant setting allows it — not implicit inside `receive_purchase`. Historical `PurchaseItem.unit_cost` must never be mutated by this.

2. Custom exceptions for domain failures (`InsufficientStockError`, `PurchaseAlreadyReceivedError`, etc.) — services raise these, views/API translate to HTTP responses.

3. Tests per spec's testing requirements for `receive_purchase` and `cancel_received_purchase`:
   - Success path.
   - Concurrent receive attempt on the same purchase (should not double-apply stock).
   - Forced failure mid-transaction (e.g. exception after stock increment, before StockMovement write) — assert full rollback, no partial state.

Do not implement views or API endpoints in this task — service layer and tests only.
