# Task: Sales & Payment Service Layer

Read `00_SPEC.md` first, specifically critical business rules 1, 2, 4, 5, 6, 8 and the testing requirements. Build against the models from `03_schema.md`.

## Produce

1. `sales/services.py` covering:
   - `complete_sale(tenant, cashier, cart_items, payment_method, discount, tax)` — atomic: for each line item, locks the stock row with `select_for_update()`, re-checks quantity under the lock (never trust a pre-lock read), raises `InsufficientStockError` if any item would go negative, decrements stock, writes `StockMovement` (type `sale_out`) per item, computes totals (subtotal, discount, tax, grand total) via a pure, separately-testable calculation function, creates `Sale` + `SaleItem` + `Payment` rows, all inside one `transaction.atomic()`. Returns the completed `Sale`.
   - `cancel_sale(sale_id, cancelled_by, reason)` — atomic: only permitted per RBAC matrix from `02_rbac_matrix.md`; writes compensating `StockMovement` (type matching a return/reversal, not an edit of `sale_out` rows), restores stock, marks `Sale.status` cancelled, never deletes the original sale record.
   - A pure function `calculate_sale_totals(line_items, discount, tax)` with no DB access — fully unit-testable in isolation, used by `complete_sale`.

2. Payment method enforcement: cashier's allowed payment methods come from tenant configuration, checked in the service layer before `Payment` is created — not just filtered in the UI.

3. Tests:
   - Success path for `complete_sale`.
   - Concurrent sale test: two simulated sales against a product with quantity=1 — exactly one must succeed, the other must raise `InsufficientStockError` and leave stock unchanged.
   - Rollback test: force a failure after stock decrement but before `Sale` creation — assert stock is restored to pre-transaction value.
   - Unit tests for `calculate_sale_totals` covering discount + tax edge cases (zero, 100% discount, rounding).

Do not implement receipt rendering or views/API in this task.
