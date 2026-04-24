from rest_framework.views import APIView
from rest_framework.response import Response

from .models import TenantOrder


class TenantOrderListView(APIView):
    """
    List orders for the current tenant.
    No explicit .filter(tenant=...) needed here because
    TenantManager handles it automatically in get_queryset().
    """

    def get(self, request):
        # This automatically scopes to the current tenant
        orders = TenantOrder.objects.all().values(
            "id", "order_number", "amount", "status", "created_at"
        )
        return Response(list(orders))
