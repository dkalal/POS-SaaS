from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import TenantMembership
from audit.models import AuditEvent
from catalog.models import Category, Product
from inventory.models import StockAdjustment
from inventory.services import create_draft_adjustment, post_adjustment
from purchasing.services import create_draft_purchase, receive_purchase
from suppliers.models import Supplier
from tenants.models import Tenant


User = get_user_model()


class AuditServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.manager = User.objects.create_user(username="manager", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.manager,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.category = Category.objects.create(
            tenant=self.tenant,
            name="Accessories",
            slug="accessories",
            is_active=True,
        )
        self.product = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("80.00"),
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            name="Main Supplier",
            is_active=True,
        )

    def test_purchase_actions_are_audited(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("45.00")}],
            created_by=self.manager,
        )
        receive_purchase(purchase.id, self.manager)

        self.assertEqual(AuditEvent.objects.filter(tenant=self.tenant, action=AuditEvent.Action.PURCHASE_CREATED).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(tenant=self.tenant, action=AuditEvent.Action.PURCHASE_RECEIVED).count(), 1)

    def test_stock_adjustment_audit_records_workflow(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            items=[{"product": self.product, "direction": "increase", "quantity": 2, "note": "Found extras"}],
            created_by=self.manager,
        )

        post_adjustment(adjustment.id, self.owner)

        self.assertEqual(
            AuditEvent.objects.filter(tenant=self.tenant, action=AuditEvent.Action.STOCK_ADJUSTMENT_CREATED).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(tenant=self.tenant, action=AuditEvent.Action.STOCK_ADJUSTMENT_POSTED).count(),
            1,
        )
        event = AuditEvent.objects.get(tenant=self.tenant, action=AuditEvent.Action.STOCK_ADJUSTMENT_POSTED)
        self.assertEqual(event.before_data["status"], StockAdjustment.Status.DRAFT)
        self.assertEqual(event.after_data["status"], StockAdjustment.Status.POSTED)
