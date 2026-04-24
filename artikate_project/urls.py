from django.contrib import admin
from django.urls import path, include
from .profiler_view import ProfilerSummaryView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Silk's template UI crashes on Python 3.14, so we serve a custom
    # JSON summary view at /silk/ that reads from Silk's database tables.
    # Silk middleware still collects profiling data in the background.
    path("silk/", ProfilerSummaryView.as_view(), name="silk-summary"),
    path("api/", include("section1.urls")),
    path("api/", include("section3.urls")),
]
