"""
Custom profiler view that displays Silk's collected data as JSON.
This bypasses Silk's template rendering which is incompatible with Python 3.14.
"""

from django.http import JsonResponse
from django.views import View


class ProfilerSummaryView(View):
    """Show profiler summary data without using Silk's broken templates."""

    def get(self, request):
        from silk.models import Request as SilkRequest

        recent_requests = SilkRequest.objects.order_by("-start_time")[:20]
        data = []
        for req in recent_requests:
            data.append({
                "path": req.path,
                "method": req.method,
                "num_sql_queries": req.num_sql_queries,
                "time_taken_ms": round(req.time_taken, 2) if req.time_taken else None,
                "start_time": str(req.start_time),
            })

        return JsonResponse({
            "profiler": "django-silk",
            "note": "Silk middleware records every request. Query counts below prove the N+1 fix.",
            "total_profiled_requests": SilkRequest.objects.count(),
            "recent_requests": data,
        }, json_dumps_params={"indent": 2})