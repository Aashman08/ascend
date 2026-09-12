# Ascend Checkout API

A REST API for creating finance terms and their insurance policies, accepting terms,
and listing agreements. Python 3.13, FastAPI, SQLAlchemy 2, and PostgreSQL 16.

## Run locally

```bash
./run.sh
```

`run.sh` checks Docker and `.env`, synchronizes the `uv` environment, starts
PostgreSQL, and launches the API. Install `uv` first if needed:

```bash
brew install uv
```

Interactive documentation is available at http://localhost:8000/docs.

If PostgreSQL is already running and the environment is synchronized, start only
the API with:

```bash
uv run uvicorn app.server:app --reload
```

Startup creates missing tables; it does not upgrade existing schemas.

## API examples

Money is returned as decimal strings. Dates are ISO 8601. The due date is inclusive
and evaluated in UTC. Replace the example due date if it is in the past.

### Create terms and policies together

```bash
curl -s -X POST localhost:8000/finance-terms \
  -H 'Content-Type: application/json' \
  -d '{
    "due_date": "2026-12-12",
    "policies": [
      {"name": "Commercial Auto", "insured_name": "Example Business", "premium": "200.00", "tax_fee": "50.00"},
      {"name": "General Liability", "insured_name": "Example Business", "premium": "300.00", "tax_fee": "50.00"}
    ]
  }'
```

Returns HTTP 201 with a UUID, `status="pending"`,
`agreed_at=null`, equal creation/update timestamps, and the policies. This example
has `total_downpayment="200.00"`, `total_amount="600.00"`, and
`amount_financed="400.00"`. Fetch it again with `GET /finance-terms/{id}`.

Both policy `name` and `insured_name` are required, trimmed, and 1–200 characters.
Premium must be positive; tax fee may be zero. Amounts accept at most two decimal
places. There must be 1–100 policies and the due date must be today or later.
Unknown fields, overflowing policy downpayments, and overflowing aggregate
downpayments are rejected with HTTP 422 before any rows are written.

### Accept on behalf of the customer

```bash
curl -s -X POST localhost:8000/finance-terms/<id>/agree
```

Returns HTTP 200 with `status="agreed"` and the acceptance
time in both `agreed_at` and `updated_at`. Acceptance locks the row so concurrent
calls make exactly one transition. Retries return HTTP 200 with the original
acceptance time, including retries after the due date has passed. Unaccepted
terms past their due date return HTTP 409 `terms_expired`.

Doing nothing leaves a pending record unchanged. Declining, editing, and canceling
terms are not supported. Business actions and their outcomes are recorded in the
audit history. Retries and rejections do not change the terms or their timestamps.

### List, filter, and sort

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
and contradictory filters return HTTP 422.

## Business audit trail

```bash
curl -s 'localhost:8000/finance-terms/<id>/audit?limit=20&offset=0'
```

Returns the same `data`/`has_more` envelope, with events ordered by increasing ID.
Each event contains the terms ID, action, outcome, database timestamp, request ID,
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

Authentication and user modeling remain out of scope. Request IDs correlate calls;
they are not verified actor identities. A future authenticated integration should
record the principal from trusted authentication context.

## Data and code organization

- `app/models.py`: finance terms, policies, and audit events. `agreed_at` is nullable
  until acceptance; status and timestamp consistency is checked by the database.
- `app/pricing.py`: `Decimal` calculations at a fixed 20% rate. Round each policy
  using `ROUND_HALF_UP` before summing. Persist downpayments for filtering and exact
  line-item display; derive total amount and financed amount in responses.
- `app/schemas.py`: body/query validation and response contracts.
- `app/client.py`: `FinanceTermsClient` owns business operations, row locking, and
  audit recording. Endpoints explicitly use
  `with create_finance_terms_client(request.state.request_id) as client:`. The factory
  creates a fresh client/session, rolls back failed operations, and always closes
  the session on exit. Cleanup does not depend on garbage collection. Methods
  return typed response models, ready for serialization after the session closes.
- `app/serializers.py`: converts database models into typed API response models.
- `app/endpoints.py`: module-level HTTP endpoints; finance endpoints each call one
  client method. The health endpoint lives here.
- `app/audit.py`: transactional audit event insertion.
- `app/errors.py`: application exception classes, including missing and expired terms.
- `app/middleware.py`: request-ID middleware only.
- `app/exception_handlers.py`: converts application exceptions into HTTP responses.
- `app/server.py`: FastAPI declaration and registration calls.
- `app/database.py`: settings, database engine/session configuration, and application
  startup table creation.

Indexes support downpayment and due-date queries, policy lookup by parent, and audit
history by terms ID/event ID. Status has no standalone index. Policy names are
checkout labels; a product catalog is not modeled.

## Errors

Application errors inherit from `APIError`. Concrete classes such as
`FinanceTermsNotFoundError` and `TermsExpiredError` define the error type and message,
so client methods simply raise the appropriate exception. There is no classification
factory or error decorator. The client factory owns rollback and session cleanup.
One shared handler in `exception_handlers.py` converts application exceptions into HTTP
responses; the same handler logs unexpected failures and returns a generic 500.
`server.py` registers it with FastAPI. Application-error responses have this shape:

```json
{"error": {"type": "invalid_state", "message": "These terms have expired. Create new terms with a current due date.", "request_id": "..."}}
```

FastAPI handles payload/query validation and routing errors using its standard
`detail` responses. HTTP 422 includes a list of errors with `loc`, `msg`, and `type`
(and other framework-provided context), for example `loc: ["body", "policies", 0, "name"]`.
Unknown routes return HTTP 404 `{"detail": "Not Found"}`; unsupported methods return
HTTP 405 with the `Allow` header. There is no custom validation handler or field-path
conversion. Unexpected application failures are logged and return a sanitized
HTTP 500 application-error response without exposing database internals. Responses include
`X-Request-ID`; the server generates a UUID for every request. Application-error
bodies also contain `request_id`;
standard framework errors carry the ID in the response header only.

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
