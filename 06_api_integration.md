# Task: API Layer (JS-InternetServices Integration)

Read `00_SPEC.md` first, specifically the "Relationship to JS-InternetServices" section and multi-tenancy section.

## Produce

1. DRF serializers and read-only viewsets under `/api/v1/` for:
   - `GET /api/v1/products/`
   - `GET /api/v1/products/{id}/`
   - `GET /api/v1/products/search/?q=...` (search priority: SKU, then name, then barcode, per spec)
   - `GET /api/v1/stock/`
   - `GET /api/v1/categories/`

2. Authentication: token-based (DRF TokenAuthentication or a scoped API-key model — pick one, justify briefly), tied to a specific tenant. Every queryset filtered by the authenticated token's tenant — cross-tenant leakage is the single most important thing to prevent here.

3. Serializer field control: cost price and profit-relevant fields must be excluded by default; only included if the token has an explicit `can_view_cost` scope/flag. State this exclusion explicitly in the serializer, don't rely on the consumer not asking.

4. Token/key revocation: a model and admin-accessible action to revoke a key immediately, plus a brief note on how `receive_purchase`/`complete_sale` are unaffected by API auth (they're internal-only, never exposed to JS-InternetServices).

5. Rate limiting: use DRF's built-in throttle classes, scoped per-token, with a sane default (state the number and justify it briefly — don't leave it unbounded).

6. Tests: one test per endpoint confirming tenant isolation (a token from tenant A cannot see tenant B's products/stock), and one confirming cost price is hidden unless scoped.

Do not implement any write/mutation endpoints — this API is read-only per spec.
