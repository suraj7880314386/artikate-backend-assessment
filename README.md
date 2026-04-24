# Artikate Studio — Backend Developer Assessment

## Quick Start (< 5 minutes)


## Optional: Live System Recording
(https://drive.google.com/file/d/1oVv75PKHKQeeujmBwnxVEyTk6eWzudg0/view?usp=sharing)

### Prerequisites
- Python 3.10+
- pip
- Redis (only needed for Section 2 live demo; all tests use fakeredis)

### Setup

```bash
# Clone and enter the project
git clone <repo-url>
cd artikate_assessment

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Run ALL tests
python manage.py test section1 section2 section3 -v2
```

### What Each Command Tests

```bash
# Section 1: N+1 query diagnosis (includes profiler evidence)
python manage.py test section1 -v2

# Section 2: Rate limiter + job queue (uses fakeredis, no Redis needed)
python manage.py test section2 -v2

# Section 3: Multi-tenant isolation
python manage.py test section3 -v2
```

### Running the Dev Server (optional)

```bash
python manage.py runserver
```

Then visit:
- `http://localhost:8000/silk/` — Django Silk profiler UI
- `http://localhost:8000/api/orders/summary/?customer_id=1` — Fixed endpoint
- `http://localhost:8000/api/orders/summary/broken/?customer_id=1` — Broken endpoint (for comparison)

### Seed Data (optional, for manual testing)

```bash
python manage.py shell -c "
from section1.tests import OrderSummaryQueryTest
OrderSummaryQueryTest.setUpTestData()
print('Seeded 50 orders with 150 items')
"
```

---

## Project Structure

```
artikate_assessment/
├── README.md              ← You are here
├── DESIGN.md              ← Section 2 architecture decisions
├── ANSWERS.md             ← Written answers for all sections
├── requirements.txt
├── manage.py
├── artikate_project/      ← Django project config
│   ├── settings.py
│   ├── urls.py
│   └── celery.py
├── section1/              ← Diagnose a Broken System
│   ├── models.py          ← Order, OrderItem, Product, Customer
│   ├── serializers.py     ← Broken vs Fixed serializers
│   ├── views.py           ← Broken vs Fixed views (with inline docs)
│   ├── tests.py           ← Query count proof with profiler output
│   └── urls.py
├── section2/              ← Rate-Limited Async Job Queue
│   ├── rate_limiter.py    ← Redis sliding-window (Lua script)
│   ├── tasks.py           ← Celery task with retry + dead-letter
│   ├── models.py          ← DeadLetterJob model
│   └── tests.py           ← 500-job test, rate limit, retry tests
├── section3/              ← Multi-Tenant Data Isolation
│   ├── models.py          ← TenantManager with auto-scoping
│   ├── tenant_context.py  ← Thread-local get/set/clear
│   ├── middleware.py       ← Tenant extraction from header/subdomain
│   ├── views.py           ← Tenant-scoped order list
│   └── tests.py           ← Isolation proof (positive + negative)
└── section4/              ← Written answers only (see ANSWERS.md)
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| SQLite for tests | Zero setup, runs everywhere. Production would use PostgreSQL. |
| fakeredis for Section 2 tests | No Redis server needed to run tests. |
| Thread-local for tenant context | Standard approach for sync Django. See ANSWERS.md for async discussion. |
| Fail-closed rate limiter | Protects the email provider API key over email latency. |
| `acks_late=True` globally | Ensures crash recovery at the cost of possible re-delivery (handled via idempotency). |
