from django.contrib import admin
from .models import Tenant, TenantOrder

admin.site.register(Tenant)
admin.site.register(TenantOrder)
