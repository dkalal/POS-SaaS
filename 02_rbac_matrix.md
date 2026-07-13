# Task: RBAC Permission Matrix

Read `00_SPEC.md` first — follow its RBAC model section (Django groups/permissions + tenant-scoped decorator, no custom engine).

## Produce

1. A full permission matrix: rows = permissions/actions, columns = Owner/Admin, Manager, Cashier. Use these as the baseline and correct/extend only where the domain clearly requires it:

   - Owner/Admin: full access including business settings, user/role management, API/integration config, all reports, void/cancel per policy.
   - Manager: products, categories, suppliers, purchases (create + receive), stock reports, sales reports, approved stock adjustments, cashier performance view. No system settings, no API keys, no hard deletes, cannot assign Owner/Admin.
   - Cashier: selling screen, product search, cart, complete sale, allowed payment methods, print receipt, view own sales/session. No product edit, no cost price visibility, no stock adjustment, no purchase creation, no supplier management, no business-wide profit view, no sale cancellation unless explicitly granted.

2. Map each permission to a concrete Django permission codename (`app_label.action_modelname` convention) or a custom permission where Django's default model permissions don't fit (e.g. `sales.cancel_sale`, `inventory.adjust_stock`).

3. State where the tenant-scoping check must additionally apply on top of the role check (it should be everywhere a queryset is touched) — give 2-3 concrete examples of the decorator/mixin pattern, not the full implementation.

4. Flag any permission in the matrix that has no enforcement point yet (e.g. permitted in the matrix but no view/service identified in `01_architecture.md` output checks it).

Output the matrix as a table plus the codename mapping. No model code yet.
