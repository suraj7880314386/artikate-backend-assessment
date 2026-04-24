# Artikate Studio — Backend Developer Assessment

**Repository:** [https://github.com/suraj7880314386/artikate-backend-assessment](https://github.com/suraj7880314386/artikate-backend-assessment)

## Optional: Live System Recording

[Live Demo Recording](https://drive.google.com/file/d/1oVv75PKHKQeeujmBwnxVEyTk6eWzudg0/view?usp=sharing)

## Quick Start (< 5 minutes)

### Prerequisites
- Python 3.10+
- pip
- Redis (only needed for Section 2 live demo; all tests use fakeredis)

### Setup

```bash
# Clone and enter the project
git clone https://github.com/suraj7880314386/artikate-backend-assessment.git
cd artikate-backend-assessment

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
- `http://localhost:8000/silk/` — Django Silk profiler summary (JSON)
- `http://localhost:8000/api/orders/summary/?customer_id=1` — Fixed endpoint
- `http://localhost:8000/api/orders/summary/broken/?customer_id=1` — Broken endpoint (for comparison)

### Seed Data (optional, for manual testing)

```bash
python manage.py shell -c "from django.contrib.auth.models import User; from section1.models import Customer, Product, Order, OrderItem; u = User.objects.create_user('testuser', password='pass'); c = Customer.objects.create(user=u, phone='1234567890'); products = [Product.objects.create(name=f'Product {i}', sku=f'SKU-{i}', price=10+i) for i in range(10)]; [OrderItem.objects.create(order=Order.objects.create(customer=c, status='confirmed', total_amount=30+i), product=products[j%10], quantity=j+1, unit_price=products[j%10].price) for i in range(50) for j in range(3)]; print('Seeded: 1 customer, 50 orders, 150 items')"
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
│   ├── celery.py
│   └── profiler_view.py
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

---


