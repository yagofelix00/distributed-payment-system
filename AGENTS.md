# AGENTS.md

## Project Overview

This repository simulates a Pix payment flow with two Flask services:

- `payment-charges-api`: main charges API. It creates and reads charges, receives signed webhooks, validates webhook security, applies idempotency, uses Redis TTL for expiration, and persists charge state.
- `fake-bank-service`: fake bank/PSP service. It simulates Pix payment processing and sends signed webhooks back to the main API with retry, exponential backoff, jitter, and DLQ handling for permanent webhook delivery failures.

The project is a portfolio/simulation with production-like backend concepts. Treat it as a realistic payment workflow, but keep in mind that some infrastructure choices are intentionally lightweight.

## Core Concepts

- Webhooks: the fake bank sends asynchronous payment confirmation events to `payment-charges-api`.
- HMAC signature validation: webhook payloads are signed and validated to protect integrity and authenticity.
- Timestamp/replay protection: webhook requests include timestamps and are rejected when outside the accepted tolerance window.
- Idempotency: duplicate webhook events are handled safely to avoid duplicate side effects.
- Redis TTL: Redis keys represent the validity/expiration window for charges and idempotency-related data.
- Lazy expiration: a pending charge can be marked expired when it is read and its Redis TTL key no longer exists.
- Charge state machine: charge status transitions must go through the existing state machine/domain flow.
- Retry with exponential backoff and jitter: `fake-bank-service` retries webhook delivery with bounded backoff and jitter.
- DLQ for failed webhooks: webhook events that cannot be delivered after retries are stored in a file-based dead-letter queue.
- `request_id` / `X-Request-Id`: request correlation is propagated across services and included in responses/log context.
- Health/readiness checks: `payment-charges-api` exposes health/readiness behavior for service and dependency checks.
- pytest test suite: the main automated suite is under `payment-charges-api/tests`.
- Docker Compose: `docker-compose.yml` runs Redis, `payment-charges-api`, and `fake-bank-service`.

## Repository Structure

- `payment-charges-api/`: main Flask API for charges, webhooks, security, Redis integration, persistence, state transitions, health/readiness, and tests.
  - `routes/`: HTTP endpoints for charges, webhooks, and health/readiness.
  - `services/`: domain/service logic, including charge state machine behavior.
  - `security/`: auth, idempotency, and webhook signature validation.
  - `infrastructure/`: Redis client integration.
  - `db_models/` and `repository/`: persistence-related code.
  - `tests/`: pytest suite for e2e flow, state machine, and webhook security.
  - `openapi.yaml`: documented API contract for the main service.
- `fake-bank-service/`: Flask service that simulates the bank/PSP side of the Pix workflow.
  - `routes/`: Pix simulation and DLQ endpoints.
  - `services/`: Pix service and webhook dispatcher.
  - `clients/`: webhook HTTP client behavior.
  - `security/`: HMAC signing helpers.
  - `dlq/`: file-based DLQ storage.
  - `openapi.yaml`: documented API contract for the fake bank service.
- `docker-compose.yml`: starts Redis plus both Flask services and wires service environment variables.
- `.github/workflows/`: GitHub Actions CI exists; `tests.yml` installs both services' dependencies and runs `pytest payment-charges-api/tests -q`.
- `.codex/agents/`: reusable Codex subagents for backend architecture, tests, security, production readiness, retry/DLQ, planning, review, debugging, requirements, API contract review, and interview explanation.

## Rules for Agents

- Do not change API contracts without explicit approval.
- Do not remove HMAC, idempotency, Redis TTL, state machine, retry, DLQ, or `request_id` propagation.
- Do not set `charge.status` directly; use the state machine or existing domain flow.
- Keep routes/controllers thin when refactoring.
- Move business logic toward services gradually.
- Prefer small, reviewable changes.
- Preserve existing tests.
- Add or update tests when behavior changes.
- Do not introduce large rewrites or overengineering.
- Understand the current flow before suggesting or implementing changes.
- Separate facts from assumptions when repository evidence is incomplete.
- Before implementing, present a short plan unless explicitly asked to edit directly.
- After implementing, summarize files changed, behavior changed, tests run, and risks.

## API Contract Rules

- Preserve endpoint paths, HTTP methods, status codes, response fields, headers, and error formats unless a contract change is explicitly approved.
- Treat any client-visible behavior change as a potential breaking change.
- Check `README.md`, service-specific `README.md` files, `openapi.yaml`, and existing tests before changing API behavior.
- Update README/OpenAPI/tests if an intentional contract change is approved.
- Prefer additive, backward-compatible changes over breaking changes.

## Testing Instructions

Use the main pytest suite:

```bash
pytest payment-charges-api/tests -q
```

Run the full local stack with Docker Compose:

```bash
docker compose up --build
```

Current repository evidence shows the main automated test suite in `payment-charges-api/tests`. No separate fake-bank-service test suite is currently present.

## Security Rules

- Never log or persist secrets.
- Do not expose `WEBHOOK_SECRET` or signatures in outputs.
- Preserve HMAC validation over webhook payloads.
- Preserve replay protection and event deduplication.
- Be careful with payment values, idempotency keys, event IDs, and charge state transitions.
- Do not weaken webhook signature validation, timestamp validation, idempotency, auth, or request correlation without explicit approval.

## Production/Portfolio Notes

- This project is a portfolio/simulation with production-like concepts.
- SQLite, the in-memory fake bank behavior, and file-based DLQ are intentional limitations for local development and demonstration.
- In real production, consider PostgreSQL, a real queue/worker system, a secret manager, structured logs, centralized observability, metrics, tracing, and durable DLQ/replay tooling.
- Keep improvements pragmatic: each change should be small enough to review and useful to explain in a backend portfolio or interview.

## Output Expectations for Codex

After any analysis or alteration, Codex should provide:

- `summary`: what was analyzed or changed.
- `files changed`: exact files created or modified.
- `tests run`: commands run and their result, or why tests were not run.
- `risks`: remaining risks, assumptions, or compatibility concerns.
- `next small task`: exactly one useful follow-up task.
