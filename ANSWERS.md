# ANSWERS.md — Written Answers for All Sections

---

## Section 1: Diagnose a Broken System

### Incident Investigation Log

**Step 1 — Confirm the symptom and scope.**
The endpoint `/api/orders/summary/` times out only for users with 200+ orders. Users with few orders are fine. This immediately suggests a query that scales with the number of orders — not a fixed infrastructure issue (DNS, network, server memory). A constant-time problem would affect all users equally.

**Step 2 — Rule out infrastructure changes.**
No code change was deployed, but "routine deployment" could mean: a migration ran, a dependency was updated, a config file changed, or a cache was flushed. I would check the migration history (`python manage.py showmigrations`) and the deployment diff.

**Step 3 — Profile the endpoint.**
I would enable `django-silk` (or `django-debug-toolbar` for the browsable API) and hit the endpoint with a test user who has 200+ orders. Silk records every SQL query, its duration, and the total count. This is the fastest way to identify whether the problem is:
- Query count (N+1) — many small queries
- Query duration (missing index) — few slow queries
- Python overhead (serializer) — few queries but slow response

**Step 4 — Identify the pattern.**
Silk output would show hundreds of nearly identical `SELECT` statements — one per order for the customer lookup, one per order for items, and one per item for the product. This is the classic **N+1 query pattern**.

**Step 5 — Trace the cause.**
The N+1 is caused by the Django ORM's lazy loading. When the serializer accesses `order.customer.user.username`, Django issues a separate `SELECT` for the `Customer` and `User` tables for each order. Similarly, iterating `order.items.all()` fires one query per order. These aren't visible in the view code — they hide inside the serializer's field access.

### Root Cause

**N+1 query caused by missing `select_related` and `prefetch_related` on the queryset.**

The "routine deployment" likely introduced a change that removed a queryset optimization. Possibilities:
1. A serializer refactor added nested `items` (with nested `product`) without updating the queryset.
2. A mixin or base view class that previously called `select_related` was replaced.
3. A migration dropped a composite index on `(customer_id, created_at)`, making even the base query slower.

### Why the Fix Works

The fix adds two ORM calls to the queryset:

1. **`select_related("customer__user")`** — Tells Django to perform a SQL `INNER JOIN` across the `Order → Customer → User` foreign key chain in a single query. The resulting row contains all three tables' columns. When the serializer later accesses `obj.customer.user.username`, Django returns the already-populated Python object from the prefetch cache — zero additional queries.

2. **`prefetch_related("items__product")`** — Tells Django to execute two separate `SELECT ... WHERE id IN (...)` queries: one for all `OrderItem` rows matching the fetched order IDs, and one for all `Product` rows matching those items' product IDs. Django then does an in-Python join, attaching items to their parent orders. This is used instead of `select_related` because items are a reverse ForeignKey (one-to-many), where a SQL JOIN would create a cartesian product and duplicate order rows.

**Before fix:** 1 + N + N + (N×M) queries (~1001 for 200 orders × 3 items)
**After fix:** 3 queries total, regardless of order/item count.

---

## Section 2: Rate-Limited Async Job Queue

### SIGKILL Recovery — What happens to in-flight tasks?

When a Celery worker process receives `SIGKILL` (kill -9), it terminates immediately with no opportunity to run signal handlers, `atexit` hooks, or `finally` blocks. The in-flight task is interrupted mid-execution.

**How our implementation handles this:**

1. **`acks_late=True`** (set globally in `settings.py` via `CELERY_TASK_ACKS_LATE` and per-task): The task message is NOT acknowledged to Redis when the worker picks it up. Instead, acknowledgement happens only after the task function returns successfully. If the worker dies, the message remains unacknowledged in Redis.

2. **`reject_on_worker_lost=True`**: When Celery's parent process detects that a worker child was lost (via the `billiard` pool monitor), it sends a `REJECT` for all unacknowledged messages from that worker. Redis then re-queues these messages for delivery to another worker.

3. **Visibility timeout** (`CELERY_BROKER_TRANSPORT_OPTIONS`): Redis has a visibility timeout (default 1 hour). If no ACK or REJECT arrives within this window, Redis automatically re-delivers the message. This is the safety net if even the parent process crashes.

4. **Idempotency consideration**: Because a task can be re-delivered, the email send operation should be idempotent. In production, this means checking a deduplication key (e.g., `order_id + email_type`) before sending, or relying on the email provider's own idempotency keys.

**What could still go wrong:** If the task partially completed before SIGKILL — e.g., the email was sent to the provider but the return/ack didn't complete — the task will be retried and the email could be sent twice. This is why idempotency at the application layer is essential for exactly-once semantics.

---

## Section 3: Multi-Tenant Data Isolation

### Failure Modes of Thread-Local Tenant Scoping in Async Django Views

Thread-local storage (`threading.local()`) binds data to a specific OS thread. This works correctly in synchronous Django because each request runs on exactly one thread from start to finish. However, **async Django views break this guarantee** in two ways:

**Failure Mode 1 — Coroutine suspension and resumption on different threads.**
When an async view uses `await` (e.g., `await database_query()`), the coroutine is suspended. When the event loop resumes it, it may run on a **different thread** in the thread pool. The thread-local tenant set by the middleware on Thread A is invisible to Thread B. The query would either see no tenant (returning empty results if we fail closed) or — worse — see a **different request's tenant** that Thread B was previously serving.

**Failure Mode 2 — Multiple coroutines sharing one thread.**
The event loop runs multiple coroutines on the same thread via cooperative multitasking. If Coroutine X sets `threading.local().tenant = TenantA` and then awaits, Coroutine Y (for a different request) runs on the same thread and overwrites it with `TenantB`. When X resumes, it reads `TenantB` — a **tenant context leak** causing cross-tenant data exposure.

**The fix: Use Python's `contextvars` module (PEP 567).**

`contextvars.ContextVar` is designed for exactly this problem. Unlike thread-locals, context variables are scoped to the **execution context** (the logical task), not the OS thread. When a coroutine is suspended and resumed, its `ContextVar` values travel with it automatically. Each `asyncio.Task` gets its own copy of the context.

```python
import contextvars

_current_tenant: contextvars.ContextVar = contextvars.ContextVar(
    "current_tenant", default=None
)

def set_current_tenant(tenant):
    _current_tenant.set(tenant)

def get_current_tenant():
    return _current_tenant.get()
```

Django 4.1+ natively supports `contextvars` in its async middleware chain. The `ASGIHandler` creates a new context copy for each request, ensuring tenant isolation even when coroutines share threads.

---

## Section 4: Written Architecture Review

### Question A — Django Admin Performance (500k+ records)

**Root Cause 1: Unoptimized `list_display` with related field traversal.**
If `ModelAdmin.list_display` includes fields like `customer__user__email`, Django executes a separate query for each row to resolve the related object. **Fix:** Add `list_select_related = ["customer__user"]` to the `ModelAdmin` class. This tells Django's admin to use `select_related()` on the changelist queryset, converting N+1 queries into a single JOIN.

**Root Cause 2: Expensive `__str__` or computed annotations on list page.**
If the model's `__str__` method accesses related objects (e.g., `return f"Order for {self.customer.name}"`), the admin calls this for every row in the list. Additionally, if a custom `list_display` method calls `obj.items.count()`, that is N+1. **Fix:** Override `get_queryset()` on the `ModelAdmin` to annotate computed values:
```python
def get_queryset(self, request):
    qs = super().get_queryset(request)
    return qs.annotate(item_count=Count("items"))
```
Then reference `item_count` in `list_display` instead of calling `.items.count()`.

**Root Cause 3: Missing `list_per_page` and unbounded count query.**
Django admin runs `SELECT COUNT(*)` to render the paginator. On 500k rows without a covering index, this is a full table scan. **Fix:** Set `list_per_page = 25` (reduce rows fetched) and critically, set `show_full_result_count = False` on the `ModelAdmin`. This skips the expensive unfiltered `COUNT(*)` query and shows "25 results (Show all)" instead of "25 of 500,000". For the remaining filtered counts, ensure `list_filter` fields have database indexes, and add `search_fields` with `db_index=True` on the searched columns.

### Question B — Pagination Trade-offs

**Offset-based pagination** (`?page=2&page_size=50`) translates to `LIMIT 50 OFFSET 50` in SQL. For page 1 this is fast, but for page 200 the database must scan and discard 10,000 rows before returning 50. This means **deeper pages get progressively slower** — O(offset) cost at the database level, because the DB engine cannot skip directly to row N without scanning preceding rows (unless there is a unique index-aligned ordering).

The other critical issue is **data mutation during pagination**. If a new order is inserted between the user fetching page 5 and page 6, all subsequent offsets shift by one — the user either sees a duplicate record or misses one entirely. For a mobile app with infinite scroll, this creates a visibly broken experience where items appear twice or disappear.

**Cursor-based pagination** (`?cursor=eyJpZCI6MTUwfQ`) uses an opaque token encoding the last-seen value (e.g., `id > 150 LIMIT 50`). The database uses the index on `id` to seek directly to the cursor position — O(1) regardless of how deep the user has scrolled. Mutations do not cause duplicates or gaps because the cursor anchors to a specific record, not a positional offset.

**The trade-off:** Cursor pagination sacrifices random access — you cannot jump to "page 47" directly, which matters for admin dashboards or tabular UIs where users expect a page selector. It also requires a stable, unique, sequential column to cursor on (typically the primary key or a `created_at` + `id` composite). If the API needs to support arbitrary sort orders (`?sort=amount`), cursor pagination becomes complex because `amount` is not unique.

**When to choose each:**
- **Mobile infinite scroll, real-time feeds, high-write tables:** Cursor-based. No performance degradation on deep scroll, mutation-safe, and mobile UIs never need "jump to page N."
- **Admin dashboards, export tools, rarely-mutated data:** Offset-based is acceptable and simpler to implement. Users expect page numbers, and the dataset is queried infrequently enough that the O(offset) cost is tolerable.

---
