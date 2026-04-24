from django.urls import path
from .views import TenantOrderListView

urlpatterns = [
    path("tenant/orders/", TenantOrderListView.as_view(), name="tenant-orders"),
]
