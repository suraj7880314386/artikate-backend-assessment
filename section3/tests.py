"""
Section 3 Tests - Multi-Tenant Data Isolation

These tests PROVE the negative: that tenant A CANNOT access tenant B's data
through any ORM call, and that .objects.all() does not bypass scoping.
"""

from django.test import TestCase, RequestFactory

from .models import Tenant, TenantOrder
from .tenant_context import set_current_tenant, clear_current_tenant
from .views import TenantOrderListView


class TenantIsolationTest(TestCase):
    """Core isolation tests for the TenantManager."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        cls.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Create orders for each tenant using unscoped manager
        # (since there's no request context during test setup)
        TenantOrder.unscoped.create(
            tenant=cls.tenant_a, order_number="A-001", amount=100
        )
        TenantOrder.unscoped.create(
            tenant=cls.tenant_a, order_number="A-002", amount=200
        )
        TenantOrder.unscoped.create(
            tenant=cls.tenant_b, order_number="B-001", amount=300
        )
        TenantOrder.unscoped.create(
            tenant=cls.tenant_b, order_number="B-002", amount=400
        )
        TenantOrder.unscoped.create(
            tenant=cls.tenant_b, order_number="B-003", amount=500
        )

    def tearDown(self):
        clear_current_tenant()

    # ── Positive tests: correct scoping ──

    def test_tenant_a_sees_only_own_orders(self):
        """Tenant A should see exactly 2 orders."""
        set_current_tenant(self.tenant_a)
        orders = TenantOrder.objects.all()
        self.assertEqual(orders.count(), 2)
        order_numbers = set(orders.values_list("order_number", flat=True))
        self.assertEqual(order_numbers, {"A-001", "A-002"})

    def test_tenant_b_sees_only_own_orders(self):
        """Tenant B should see exactly 3 orders."""
        set_current_tenant(self.tenant_b)
        orders = TenantOrder.objects.all()
        self.assertEqual(orders.count(), 3)
        order_numbers = set(orders.values_list("order_number", flat=True))
        self.assertEqual(order_numbers, {"B-001", "B-002", "B-003"})

    # ── Negative tests: PROVE isolation cannot be bypassed ──

    def test_tenant_a_cannot_see_tenant_b_data(self):
        """Tenant A must NOT see any of Tenant B's orders."""
        set_current_tenant(self.tenant_a)
        b_orders = TenantOrder.objects.filter(tenant=self.tenant_b)
        self.assertEqual(
            b_orders.count(), 0,
            "Tenant A should not see Tenant B's orders even with explicit filter"
        )

    def test_objects_all_does_not_bypass_scoping(self):
        """
        .objects.all() must return scoped results, not all records.
        Total records = 5, but scoped should return only 2 for tenant A.
        """
        set_current_tenant(self.tenant_a)
        all_orders = TenantOrder.objects.all()
        total_unscoped = TenantOrder.unscoped.all().count()

        self.assertEqual(all_orders.count(), 2)
        self.assertEqual(total_unscoped, 5)
        self.assertNotEqual(
            all_orders.count(), total_unscoped,
            ".objects.all() must NOT return unscoped results"
        )

    def test_filter_cannot_cross_tenant_boundary(self):
        """Even explicit filter by another tenant's order number returns nothing."""
        set_current_tenant(self.tenant_a)
        cross_tenant = TenantOrder.objects.filter(order_number="B-001")
        self.assertEqual(
            cross_tenant.count(), 0,
            "Should not find Tenant B's order from Tenant A's context"
        )

    def test_get_raises_for_cross_tenant_access(self):
        """Attempting to .get() another tenant's order should raise DoesNotExist."""
        set_current_tenant(self.tenant_a)
        b_order = TenantOrder.unscoped.filter(order_number="B-001").first()

        with self.assertRaises(TenantOrder.DoesNotExist):
            TenantOrder.objects.get(pk=b_order.pk)

    def test_no_tenant_context_returns_empty(self):
        """
        When no tenant is set (e.g., misconfigured middleware),
        the manager returns an empty queryset (fail closed).
        """
        clear_current_tenant()
        orders = TenantOrder.objects.all()
        self.assertEqual(
            orders.count(), 0,
            "No tenant context should return empty, not all records"
        )

    def test_count_is_scoped(self):
        """Even aggregate operations like .count() respect tenant scoping."""
        set_current_tenant(self.tenant_b)
        self.assertEqual(TenantOrder.objects.count(), 3)

    def test_exists_is_scoped(self):
        """exists() respects tenant scoping."""
        set_current_tenant(self.tenant_a)
        self.assertTrue(TenantOrder.objects.filter(order_number="A-001").exists())
        self.assertFalse(TenantOrder.objects.filter(order_number="B-001").exists())


class TenantMiddlewareTest(TestCase):
    """Test that the middleware correctly resolves tenant from headers."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = Tenant.objects.create(name="Alpha", slug="alpha")
        cls.tenant_b = Tenant.objects.create(name="Beta", slug="beta")

        TenantOrder.unscoped.create(
            tenant=cls.tenant_a, order_number="ALPHA-1", amount=100
        )
        TenantOrder.unscoped.create(
            tenant=cls.tenant_b, order_number="BETA-1", amount=200
        )

    def test_header_based_tenant_resolution(self):
        """X-Tenant-ID header should scope the response."""
        factory = RequestFactory()

        # Request as tenant A
        request = factory.get(
            "/api/tenant/orders/",
            HTTP_X_TENANT_ID=str(self.tenant_a.pk),
        )

        from .middleware import TenantMiddleware
        from .views import TenantOrderListView

        middleware = TenantMiddleware(
            get_response=lambda req: TenantOrderListView.as_view()(req)
        )
        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        order_numbers = [o["order_number"] for o in response.data]
        self.assertIn("ALPHA-1", order_numbers)
        self.assertNotIn("BETA-1", order_numbers)

    def test_middleware_cleans_up_on_exception(self):
        """Tenant context is cleared even if the view raises."""
        from .tenant_context import get_current_tenant

        factory = RequestFactory()
        request = factory.get(
            "/api/tenant/orders/",
            HTTP_X_TENANT_ID=str(self.tenant_a.pk),
        )

        def failing_view(req):
            raise ValueError("Intentional test error")

        from .middleware import TenantMiddleware

        middleware = TenantMiddleware(get_response=failing_view)

        with self.assertRaises(ValueError):
            middleware(request)

        # Tenant context MUST be cleared after the exception
        self.assertIsNone(
            get_current_tenant(),
            "Tenant context must be cleared even after exception"
        )
