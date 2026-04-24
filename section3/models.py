"""
Multi-Tenant Models with Automatic Queryset Scoping

The TenantManager overrides get_queryset() so that EVERY query
through Order.objects automatically filters by the current tenant.
This means:
  - Order.objects.all()        -> only current tenant's orders
  - Order.objects.filter(...)  -> scoped to current tenant
  - Order.objects.count()      -> only current tenant's count

A developer CANNOT accidentally forget the tenant filter because
it is injected at the Manager level, before any view code runs.

For admin/management commands that need unscoped access,
TenantUnscopedManager is available as Order.unscoped.
"""

from django.db import models
from .tenant_context import get_current_tenant


class Tenant(models.Model):
    """Represents a tenant (client organization) in the SaaS platform."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TenantQuerySet(models.QuerySet):
    """QuerySet that auto-filters by current tenant."""
    pass


class TenantManager(models.Manager):
    """
    Custom manager that automatically scopes ALL querysets to the
    current tenant. This is the core enforcement mechanism.

    How it works:
      1. get_queryset() is called for every ORM operation
         (all, filter, get, count, exists, aggregate, etc.)
      2. We call super().get_queryset() to get the base QuerySet
      3. If a tenant is set in thread-local context, we apply
         .filter(tenant=current_tenant)
      4. If no tenant is set (e.g., management commands, migrations),
         we return an EMPTY queryset as a safety default.
         This prevents accidental data exposure.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            return qs.filter(tenant=tenant)
        # Safety: if no tenant context, return nothing rather than everything.
        # This is the "fail closed" approach: forgetting to set context
        # returns no data rather than all tenants' data.
        return qs.none()


class TenantUnscopedManager(models.Manager):
    """
    Unscoped manager for admin and management command use.
    Accessible as Model.unscoped.all()
    """
    pass


class TenantOrder(models.Model):
    """
    Order model with automatic tenant scoping.

    Usage:
        # In a request with tenant context set:
        TenantOrder.objects.all()  # -> only current tenant's orders
        TenantOrder.objects.filter(status="pending")  # -> scoped

        # For admin/migrations:
        TenantOrder.unscoped.all()  # -> all orders, no filter
    """
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="orders"
    )
    order_number = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    # Default manager: auto-scoped by tenant
    objects = TenantManager()

    # Escape hatch for admin / management commands
    unscoped = TenantUnscopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
        ]

    def __str__(self):
        return f"Order {self.order_number} (Tenant: {self.tenant.slug})"
