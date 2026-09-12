# Repository Guidelines

## Project Structure & Module Organization

This Python 3.13 API uses FastAPI, SQLAlchemy 2, and PostgreSQL. Source lives in
`app/`:

- `server.py` creates the FastAPI application and registers middleware, routes,
  and exception handlers.
- `endpoints.py` contains HTTP route definitions.
- `client.py` contains finance-terms operations, transactions, and database access.
- `database.py` loads settings, creates the engine/session factory, and initializes
  tables at application startup.
- `models.py` contains SQLAlchemy persistence models.
- `schemas.py` contains Pydantic request and response contracts.
- `pricing.py` owns money calculations and amount-limit rules.
- `serializers.py` converts database models into API response models.
- `audit.py` records transactional business events.
- `errors.py`, `exception_handlers.py`, and `middleware.py` contain application
  errors, HTTP error rendering, and server-generated request IDs.

Tests live in `tests/`. There is no frontend or asset pipeline.

## Build, Test, and Development Commands

Run commands from the repository root:

- `docker compose up -d --wait`: start PostgreSQL.
- `uv sync`: create/synchronize the project environment from `pyproject.toml` and
  `uv.lock`.
- `uv run uvicorn app.server:app --reload`: run the API locally.
- `uv run pytest -v`: run the complete test suite.
- `uv run pytest tests/test_pricing.py -v`: run pricing tests only.

Interactive API documentation is available at `http://localhost:8000/docs`.
Application startup creates database tables automatically. No separate build step
is configured.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, `snake_case` functions/modules,
`PascalCase` classes, and uppercase constants. Group standard-library,
third-party, and local imports separately. Keep route functions thin and keep
business/database operations in `client.py`.

Represent money with `Decimal`, constructed from strings. Preserve per-policy
`ROUND_HALF_UP` rounding before summing and serialize amounts as decimal strings.
Keep money formulas in `pricing.py`; do not duplicate them in routes or schemas.

## Testing Guidelines

Use pytest with `test_*.py` files. Keep test names focused on behavior and use
short docstrings where additional context helps. Add pure money cases to
`test_pricing.py`; add HTTP, transaction, audit, and persistence behavior to
`test_finance_terms_api.py`; add database-free contract checks to
`test_validation.py` and `test_errors.py`.

Integration tests create a unique PostgreSQL schema per test, create the tables,
and drop the schema afterward. Keep `TEST_DATABASE_URL` pointed at a disposable
test database. Pricing and validation tests should not require database access.

## Configuration

Create a local `.env` file with:

- `DATABASE_URL`: application PostgreSQL connection string.
- `TEST_DATABASE_URL`: disposable PostgreSQL connection string used by tests.

These settings are required; do not hardcode database URLs in application code.
Keep `.env` untracked and keep development and test databases distinct.

## Commit & Pull Request Guidelines

Use short, imperative commit subjects such as `Fix agreement expiry validation`.
Describe the problem, behavior change, and test results in pull requests. Update
README examples when API contracts change.
