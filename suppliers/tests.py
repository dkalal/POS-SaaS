from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from suppliers.models import Supplier
from tenants.models import Tenant


User = get_user_model()


class SupplierCrudTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.manager = User.objects.create_user(username="manager", password="pass12345")
        self.cashier = User.objects.create_user(username="cashier", password="pass12345")
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
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            role=TenantMembership.Role.CASHIER,
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            name="Main Supplier",
            supplier_code="SUP-001",
            phone="+255700000000",
            email="supplier@example.com",
            is_active=True,
        )

    def _login_as(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

    def test_manager_can_create_edit_and_toggle_suppliers(self):
        self._login_as(self.manager)

        response = self.client.get(reverse("suppliers:supplier-list"))
        self.assertEqual(response.status_code, 200)

        created = self.client.post(
            reverse("suppliers:supplier-list"),
            data={
                "name": "Office Goods",
                "supplier_code": "SUP-002",
                "phone": "+255711111111",
                "email": "office@example.com",
                "address": "Market Street",
                "notes": "Preferred vendor",
                "is_active": "on",
            },
        )
        self.assertEqual(created.status_code, 302)
        self.assertTrue(Supplier.objects.filter(tenant=self.tenant, name="Office Goods").exists())

        updated = self.client.post(
            reverse("suppliers:supplier-edit", args=[self.supplier.id]),
            data={
                "name": "Main Supplier Co.",
                "supplier_code": "SUP-001",
                "phone": "+255722222222",
                "email": "supplier@example.com",
                "address": "Warehouse Road",
                "notes": "Updated",
                "is_active": "on",
            },
        )
        self.assertEqual(updated.status_code, 302)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, "Main Supplier Co.")
        self.assertEqual(self.supplier.phone, "+255722222222")

        toggled = self.client.post(reverse("suppliers:supplier-toggle-active", args=[self.supplier.id]))
        self.assertEqual(toggled.status_code, 302)
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_active)

    def test_supplier_list_filters_and_paginates(self):
        self._login_as(self.manager)
        for index in range(13):
            Supplier.objects.create(
                tenant=self.tenant,
                name=f"Supplier {index:02d}",
                supplier_code=f"SUP-{index:03d}",
                phone=f"+25570000{index:03d}",
                email=f"supplier{index:02d}@example.com",
                is_active=index % 2 == 0,
            )

        response = self.client.get(reverse("suppliers:supplier-list"), {"q": "Supplier 12", "status": "active"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier 12")
        self.assertNotContains(response, "Supplier 11")

        paginated = self.client.get(reverse("suppliers:supplier-list"), {"page": 2})
        self.assertEqual(paginated.status_code, 200)
        self.assertContains(paginated, "Supplier 11")
        self.assertNotContains(paginated, "Supplier 00")

    def test_cashier_cannot_access_supplier_management(self):
        self._login_as(self.cashier)

        response = self.client.get(reverse("suppliers:supplier-list"))

        self.assertEqual(response.status_code, 403)
