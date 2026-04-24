"""
Tenant Middleware — Extracts tenant from request and binds it to thread-local.

Tenant identification strategy (in priority order):
  1. X-Tenant-ID header (for API clients / JWT-authenticated requests)
  2. Subdomain extraction (e.g., acme.example.com -> tenant slug "acme")

The middleware sets the tenant context BEFORE the view runs and
ALWAYS clears it in the finally block, even if the view raises.
This prevents tenant leakage between requests on the same thread
(Django reuses threads in WSGI servers like gunicorn).
"""

from django.http import JsonResponse
from .tenant_context import set_current_tenant, clear_current_tenant


class TenantMiddleware:
    """
    Middleware that extracts tenant from the request and sets it
    in thread-local storage for automatic queryset scoping.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            tenant = self._resolve_tenant(request)
            set_current_tenant(tenant)
            response = self.get_response(request)
            return response
        finally:
            # CRITICAL: Always clear, even on exception.
            # Without this, a subsequent request on the same thread
            # could inherit the previous request's tenant.
            clear_current_tenant()

    def _resolve_tenant(self, request):
        """
        Resolve the tenant from the request.

        Strategy 1: X-Tenant-ID header (API / JWT flow)
        Strategy 2: Subdomain (multi-tenant SaaS pattern)

        Returns the Tenant model instance, or None for unauthenticated
        / tenant-less requests (admin, health checks, etc.)
        """
        from .models import Tenant

        # Strategy 1: Explicit header
        tenant_id = request.META.get("HTTP_X_TENANT_ID")
        if tenant_id:
            try:
                return Tenant.objects.get(pk=tenant_id)
            except (Tenant.DoesNotExist, ValueError):
                return None

        # Strategy 2: Subdomain
        host = request.get_host().split(":")[0]  # strip port
        parts = host.split(".")
        if len(parts) > 2:
            subdomain = parts[0]
            try:
                return Tenant.objects.get(slug=subdomain)
            except Tenant.DoesNotExist:
                return None

        return None
