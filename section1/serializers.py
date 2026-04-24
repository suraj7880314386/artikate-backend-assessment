from rest_framework import serializers
from .models import Order, OrderItem, Product, Customer


# ──────────────────────────────────────────────
#  BROKEN serializers (cause N+1 queries)
# ──────────────────────────────────────────────

class BrokenProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "sku", "price"]


class BrokenOrderItemSerializer(serializers.ModelSerializer):
    # Accessing product here triggers a separate query per item
    # because the queryset did not prefetch/select the product relation.
    product = BrokenProductSerializer()

    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity", "unit_price"]


class BrokenOrderSummarySerializer(serializers.ModelSerializer):
    # Nested serializer accesses .items.all() for EACH order (N+1)
    # and then each item accesses .product (another N+1 layer).
    items = BrokenOrderItemSerializer(many=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "status", "total_amount",
            "created_at", "customer_name", "items",
        ]

    def get_customer_name(self, obj):
        # This causes ANOTHER query per order because customer
        # and customer.user are not select_related.
        return obj.customer.user.username


# ──────────────────────────────────────────────
#  FIXED serializers (same output, no extra queries)
# ──────────────────────────────────────────────

class FixedProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "sku", "price"]


class FixedOrderItemSerializer(serializers.ModelSerializer):
    product = FixedProductSerializer()

    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity", "unit_price"]


class FixedOrderSummarySerializer(serializers.ModelSerializer):
    items = FixedOrderItemSerializer(many=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "status", "total_amount",
            "created_at", "customer_name", "items",
        ]

    def get_customer_name(self, obj):
        # Works without extra queries because the view uses
        # select_related("customer__user")
        return obj.customer.user.username
