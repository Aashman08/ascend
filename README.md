# Ascend Checkout API

A REST API for creating finance terms and their insurance policies, accepting terms,
and listing agreements. Python 3.13, FastAPI, SQLAlchemy 2, and PostgreSQL 16.

## Run locally

### Prerequisites

Install and start:

- Docker Desktop
- `uv`

On macOS, install `uv` with:

```bash
brew install uv
```

### Start the application

```bash
./run.sh
```

`run.sh` verifies Docker and `uv`, creates `.env` from `.env.example` if it does
not exist, synchronizes the Python environment, starts PostgreSQL, and launches
the API. It never overwrites an existing `.env`. `.env` is ignored by Git and
should contain only local configuration.

PostgreSQL runs in the pinned `postgres:16-alpine` Docker image with the
development credentials and database names from `.env.example`. This makes the
database setup consistent across environments. The recipient still needs Docker
Desktop, `uv`, and an available host port `5432`.

Interactive documentation is available at http://localhost:8000/docs.

If PostgreSQL is already running and the environment is synchronized, start only
the API with:

```bash
uv run uvicorn app.server:app --reload
```

Startup creates missing tables; it does not upgrade existing schemas.

## APIs

Money is returned as decimal strings. Dates are ISO 8601. The due date is inclusive
and evaluated in UTC. Replace the example due date if it is in the past.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/finance-terms` | Create finance terms and their policies. |
| `GET` | `/finance-terms` | List terms with filtering, sorting, and pagination. |
| `GET` | `/finance-terms/{id}` | Fetch one finance-terms record. |
| `POST` | `/finance-terms/{id}/agree` | Agree to pending terms. |
| `GET` | `/audit` | List audit events or filter by finance-terms ID. |
| `GET` | `/health` | Check whether the API is running. |

### 1. Create terms and policies

**Request fields**

| Field | Type | Required | Description |
|---|---|---:|---|
| `due_date` | string | Yes | `YYYY-MM-DD`, today or later. |
| `policies` | array | Yes | 1–100 policy objects. |
| `policies[].name` | string | Yes | Trimmed, 1–200 characters. |
| `policies[].insured_name` | string | Yes | Trimmed, 1–200 characters. |
| `policies[].premium` | decimal string | Yes | Greater than zero, up to 2 decimal places. |
| `policies[].tax_fee` | decimal string | Yes | Zero or greater, up to 2 decimal places. |

**Example**

```bash
curl -s -X POST 'http://localhost:8000/finance-terms' \
  -H 'Content-Type: application/json' \
  -d '{
    "due_date": "2026-12-12",
    "policies": [
      {"name": "Commercial Auto", "insured_name": "Example Business", "premium": "200.00", "tax_fee": "50.00"},
      {"name": "General Liability", "insured_name": "Example Business", "premium": "300.00", "tax_fee": "50.00"}
    ]
  }'
```

Returns HTTP 201 with a UUID, `status="pending"`, the policies, and calculated
amounts. Unknown fields and amount overflows return HTTP 422.

### 2. Get one finance-terms record

```bash
curl -s 'localhost:8000/finance-terms/<id>'
```

Returns HTTP 200 with the finance-terms record and its policies. A missing ID
returns HTTP 404.

### 3. Agree to terms

```bash
curl -s -X POST 'http://localhost:8000/finance-terms/<id>/agree'
```

Returns HTTP 200 with `status="agreed"` and the acceptance time in both
`agreed_at` and `updated_at`. A missing ID returns HTTP 404. Expired terms return
HTTP 409. Repeating an agreement returns HTTP 200 with the original timestamp.

Doing nothing leaves a pending record unchanged. Declining, editing, and canceling
terms are not supported. Business actions and their outcomes are recorded in the
audit history. Retries and rejections do not change the terms or their timestamps.
Each acceptance request gets its own audit event: the first acceptance records
`agree / succeeded`, and subsequent requests record `agree / unchanged`.

### 4. List, filter, and sort

```bash
curl -s 'localhost:8000/finance-terms?downpayment_gt=150&status=pending&sort=due_date&order=desc&limit=20&offset=0'
```

| Parameter | Values |
|---|---|
| `downpayment_gt`, `downpayment_lt`, `downpayment_eq` | Nonnegative decimal amount; equality cannot combine with range filters |
| `status` | `pending` or `agreed` |
| `sort` | `downpayment` or `due_date` (default) |
| `order` | `asc` (default) or `desc` |
| `limit` | 1–100, default 20 |
| `offset` | Nonnegative, default 0 |

Returns `{"data": [...], "has_more": true}`. Add `limit` to `offset` for the next
page. A UUID breaks sorting ties. Offset pagination keeps this exercise simple;
concurrent inserts or deletions can shift page boundaries. Unknown query fields
and contradictory filters return HTTP 422. Negative amounts return a field-specific
validation error.

### 5. List audit events

```bash
# All audit events
curl -s 'localhost:8000/audit?limit=20&offset=0'

# Only events for one finance-terms agreement
curl -s 'localhost:8000/audit?finance_terms_id=<id>&limit=20&offset=0'
```

Returns the same `data`/`has_more` envelope, with events ordered by increasing ID.
Omit `finance_terms_id` to list events across all agreements; supply a UUID to filter.
`limit` defaults to 20 (maximum 100) and `offset` defaults to 0. Each event contains
the terms ID, action, outcome, database timestamp, request ID,
and JSON details. The history endpoint also works for missing terms IDs, allowing
inspection of rejected attempts; an ID with no history returns an empty list.

| Action | Outcome | Recorded details |
|---|---|---|
| `create` | `succeeded` | Initial policies, amounts, due date, status, and fixed pricing rate |
| `agree` | `succeeded` | Previous/new status and acceptance timestamp |
| `agree` | `unchanged` | Retry of an already accepted agreement |
| `agree` | `rejected` | Expired terms or nonexistent terms ID |

Successful mutations and their audit events commit together. An audit write failure
rolls back the mutation. Business rejections commit their event before returning
the error. Malformed requests and reads use ordinary server access logs; they do
not create business events. Infrastructure failures go to application logs because
an unavailable database cannot reliably record its own failures.

Audit rows deliberately have no foreign key: they can refer to missing IDs and
survive deletion of a parent. There is no audit mutation API, so audit history is
append-only by application convention. The Docker role is for local development;
production would need a restricted application role and separately controlled
backups.

### 6. Health check

```bash
curl -s 'localhost:8000/health'
```

Returns:

```json
{"status": "ok"}
```

Authentication and user modeling remain out of scope. Request IDs correlate calls;
they are not verified actor identities. A future authenticated integration should
record the principal from trusted authentication context.

## Data and code organization

| Module | Responsibility |
|---|---|
| `app/models.py` | SQLAlchemy models for finance terms, policies, and audit events. Database constraints enforce status/timestamp consistency. |
| `app/pricing.py` | `Decimal` calculations at a fixed 20% rate, `ROUND_HALF_UP` rounding, and amount-limit rules. |
| `app/schemas.py` | Request body/query validation and API response contracts. |
| `app/client.py` | Finance-terms operations, row locking, transactions, and audit recording. |
| `app/serializers.py` | Converts database models into typed API response models. |
| `app/endpoints.py` | HTTP route definitions for finance terms, audit history, and health checks. |
| `app/audit.py` | Transactional audit event insertion. |
| `app/errors.py` | Application exception classes, including missing and expired terms. |
| `app/middleware.py` | Server-generated request-ID middleware. |
| `app/exception_handlers.py` | Converts application exceptions into HTTP error responses. |
| `app/server.py` | FastAPI application declaration and component registration. |
| `app/database.py` | Settings, database engine/session configuration, and startup table creation. |

Indexes support downpayment and due-date queries, policy lookup by parent, and audit
history by terms ID/event ID. Status has no standalone index. Policy names are
checkout labels; a product catalog is not modeled.

## Errors

| HTTP status | Error type | Meaning | Response shape |
|---:|---|---|---|
| `404` | `not_found` | Requested finance terms do not exist. | Application error |
| `409` | `invalid_state` | Finance terms have expired and cannot be agreed. | Application error |
| `422` | `validation_error` | Request body, query parameter, or path parameter is invalid. | FastAPI standard response |
| `405` | — | HTTP method is not supported for the route. | FastAPI standard response |
| `500` | `internal_error` | Unexpected application or infrastructure failure. | Sanitized application error |

### Application errors

Application exceptions inherit from `APIError`. The shared handler in
`exception_handlers.py` converts them into this response:

```json
{
  "error": {
    "type": "invalid_state",
    "message": "These terms have expired. Create new terms with a current due date.",
    "request_id": "..."
  }
}
```

### Validation and routing errors

FastAPI returns standard `detail` responses:

```json
{
  "detail": [
    {
      "loc": ["body", "policies", 0, "name"],
      "msg": "String should have at least 1 character",
      "type": "string_too_short"
    }
  ]
}
```

Unknown routes return `404 {"detail": "Not Found"}`. Unsupported methods return
HTTP 405 with an `Allow` header.

### Request IDs

The server generates a UUID for every request and returns it in `X-Request-ID`.
Application-error bodies also include `request_id`; standard FastAPI errors carry
the ID in the response header only.

## Testing

```bash
uv run pytest -v
uv run pytest tests/test_pricing.py tests/test_validation.py tests/test_errors.py -v
```

The second command needs no running database. Integration tests use
`TEST_DATABASE_URL`, creating and dropping isolated schemas inside that database.
They exercise acceptance concurrency, retries/rejections, monetary validation,
audit recording, and transaction rollback.

Creation itself has no idempotency key: retrying creation can create another
agreement. Authentication and tenancy are outside this exercise. Audit history and its tests extend the original take-home scope.

## Things to Add On

**Cancellation and replacement terms:** A future cancellation operation could mark
pending terms as cancelled, prevent acceptance, and record the action and reason
in the audit trail. Revised terms would be created as a new agreement, optionally
linked to the cancelled one, preserving the original terms and policies. Handling
already agreed terms would require a separate business decision. Cancellation is
not implemented because it falls outside the assessment's three user stories.
