"""
Tenant Context — Thread-local storage for current tenant.

This module provides get/set functions for the current tenant,
stored in threading.local(). The middleware sets this at the
start of each request and clears it at the end.

IMPORTANT: This approach is NOT safe for async Django views.
See ANSWERS.md for a full explanation and the contextvars alternative.
"""

import threading

_thread_local = threading.local()


def set_current_tenant(tenant):
    """Set the tenant for the current thread/request."""
    _thread_local.tenant = tenant


def get_current_tenant():
    """
    Get the tenant for the current thread/request.
    Returns None if no tenant is set (e.g., management commands).
    """
    return getattr(_thread_local, "tenant", None)


def clear_current_tenant():
    """Clear tenant context. Called in middleware finally block."""
    _thread_local.tenant = None
