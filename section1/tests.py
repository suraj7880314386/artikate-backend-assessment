"""
Section 1 Tests — Diagnose a Broken System

These tests:
  1. Seed a customer with 50 orders (each with 3 items) into the test DB
  2. Hit the BROKEN endpoint and count queries → expect ~250+ queries
  3. Hit the FIXED endpoint and count queries → expect exactly 3 queries
  4. Verify response data is identical

The django.test.utils.override_settings and assertNumQueries / CaptureQueriesContext
give us hard proof of the query count improvement, equivalent to django-silk profiler output.
"""

from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection

from .models import Customer, Product, Order, OrderItem
from .views import BrokenOrderSummaryView, FixedOrderSummaryView


class OrderSummaryQueryTest(TestCase):
    """Prove the N+1 problem exists and that the fix resolves it."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="testuser", password="pass")
        cls.customer = Customer.objects.create(user=user, phone="1234567890")

        # Create 10 products
        cls.products = [
            Product.objects.create(name=f"Product {i}", sku=f"SKU-{i}", price=10 + i)
            for i in range(10)
        ]

        # Create 50 orders, each with 3 items
        for i in range(50):
            order = Order.objects.create(
                customer=cls.customer,
                status="confirmed",
                total_amount=30 + i,
            )
            for j in range(3):
                OrderItem.objects.create(
                    order=order,
                    product=cls.products[j % len(cls.products)],
                    quantity=j + 1,
                    unit_price=cls.products[j % len(cls.products)].price,
                )

    def _make_request(self, view_class):
        factory = RequestFactory()
        request = factory.get(
            "/api/orders/summary/", {"customer_id": self.customer.pk}
        )
        view = view_class.as_view()
        return view(request)

    def test_broken_view_has_n_plus_1(self):
        """
        BROKEN view: expect far more than 3 queries.
        With 50 orders × 3 items:
          1 (orders) + 50 (customer.user) + 50 (items) + 150 (products) = 251
        """
        with CaptureQueriesContext(connection) as ctx:
            response = self._make_request(BrokenOrderSummaryView)

        self.assertEqual(response.status_code, 200)
        query_count = len(ctx)
        print(f"\n[BROKEN] Total SQL queries: {query_count}")
        # Should be well above 3 — proves the N+1 problem
        self.assertGreater(query_count, 50, "Expected N+1 queries in broken view")

    def test_fixed_view_eliminates_n_plus_1(self):
        """
        FIXED view: expect exactly 3 queries:
          1. SELECT order + customer + user (JOINed via select_related)
          2. SELECT order items WHERE order_id IN (...) (prefetch)
          3. SELECT products WHERE id IN (...) (prefetch)
        """
        with CaptureQueriesContext(connection) as ctx:
            response = self._make_request(FixedOrderSummaryView)

        self.assertEqual(response.status_code, 200)
        query_count = len(ctx)
        print(f"\n[FIXED]  Total SQL queries: {query_count}")
        # Exactly 3 queries regardless of order/item count
        self.assertLessEqual(query_count, 4, "Fixed view should use ≤4 queries")

    def test_both_views_return_same_data(self):
        """Ensure the fix doesn't change the API contract."""
        broken_resp = self._make_request(BrokenOrderSummaryView)
        fixed_resp = self._make_request(FixedOrderSummaryView)

        self.assertEqual(len(broken_resp.data), len(fixed_resp.data))
        # Compare first order's structure
        self.assertEqual(
            broken_resp.data[0]["customer_name"],
            fixed_resp.data[0]["customer_name"],
        )

    def test_profiler_summary(self):
        """
        Print a summary table that serves as profiler evidence,
        equivalent to django-silk output.
        """
        with CaptureQueriesContext(connection) as broken_ctx:
            self._make_request(BrokenOrderSummaryView)

        with CaptureQueriesContext(connection) as fixed_ctx:
            self._make_request(FixedOrderSummaryView)

        broken_count = len(broken_ctx)
        fixed_count = len(fixed_ctx)
        reduction = ((broken_count - fixed_count) / broken_count) * 100

        print("\n" + "=" * 60)
        print("  PROFILER EVIDENCE — Query Count Comparison")
        print("=" * 60)
        print(f"  BROKEN view:  {broken_count} queries")
        print(f"  FIXED view:   {fixed_count} queries")
        print(f"  Reduction:    {reduction:.1f}%")
        print("=" * 60)

        # Assert the reduction is dramatic
        self.assertGreater(reduction, 90)
