from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Order
from .serializers import BrokenOrderSummarySerializer, FixedOrderSummarySerializer


class BrokenOrderSummaryView(APIView):
    """
    BROKEN VIEW — demonstrates the N+1 query problem.

    This view fetches all orders for a customer using a bare
    Order.objects.filter(...) with no select_related or prefetch_related.

    The serializer then:
      1. Accesses .customer.user.username → 1 query per order (N+1 on customer+user)
      2. Iterates .items.all() → 1 query per order (N+1 on order items)
      3. Accesses .product on each item → 1 query per item (N+1 on product)

    For a customer with 200 orders averaging 3 items each:
      1 (initial) + 200 (customer) + 200 (items) + 600 (products) = ~1001 queries

    After a "routine deployment" this could surface if, for example:
      - A migration removed a database index on (customer_id, created_at)
      - The serializer was refactored to include nested items (previously flat)
      - A queryset mixin that previously added select_related was removed
    """

    def get(self, request):
        customer_id = request.query_params.get("customer_id")
        if not customer_id:
            return Response(
                {"error": "customer_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # BUG: No select_related or prefetch_related — this is the root cause.
        orders = Order.objects.filter(customer_id=customer_id)

        serializer = BrokenOrderSummarySerializer(orders, many=True)
        return Response(serializer.data)


class FixedOrderSummaryView(APIView):
    """
    FIXED VIEW — eliminates N+1 queries using select_related + prefetch_related.

    How the fix works at the database / ORM level:
    ─────────────────────────────────────────────

    1. select_related("customer__user"):
       Generates a single SQL query with INNER JOINs:
         SELECT order.*, customer.*, user.*
         FROM section1_order
         INNER JOIN section1_customer ON ...
         INNER JOIN auth_user ON ...
         WHERE order.customer_id = %s

       This means when the serializer accesses obj.customer.user.username,
       Django returns the already-loaded Python object — no additional query.
       select_related works via JOIN and is ideal for ForeignKey / OneToOne.

    2. prefetch_related("items__product"):
       Executes exactly 2 additional queries (not N):
         Query A: SELECT * FROM section1_orderitem
                  WHERE order_id IN (1, 2, 3, ... 200)
         Query B: SELECT * FROM section1_product
                  WHERE id IN (5, 12, 33, ...)

       Django then performs an in-Python JOIN, attaching each item to its
       parent order and each product to its item via the prefetch cache.
       prefetch_related is the correct tool for reverse FK / M2M relations
       where a JOIN would create a cartesian product.

    Result: Total queries = 3 (one joined query + two prefetch queries),
    regardless of how many orders or items exist. The 1001-query problem
    is completely eliminated.
    """

    def get(self, request):
        customer_id = request.query_params.get("customer_id")
        if not customer_id:
            return Response(
                {"error": "customer_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        orders = (
            Order.objects.filter(customer_id=customer_id)
            .select_related("customer__user")        # JOIN customer + user
            .prefetch_related("items__product")       # 2 extra queries total
            .order_by("-created_at")
        )

        serializer = FixedOrderSummarySerializer(orders, many=True)
        return Response(serializer.data)
