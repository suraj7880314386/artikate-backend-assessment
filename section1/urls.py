from django.urls import path
from .views import BrokenOrderSummaryView, FixedOrderSummaryView

urlpatterns = [
    path(
        "orders/summary/broken/",
        BrokenOrderSummaryView.as_view(),
        name="order-summary-broken",
    ),
    path(
        "orders/summary/",
        FixedOrderSummaryView.as_view(),
        name="order-summary-fixed",
    ),
]
