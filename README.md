# Medical Device Data Aggregation System

Backend for ingesting, validating, storing and aggregating physiological data from medical
devices, with **asynchronous clinical alerting** and rule-based **risk scoring**.

**Async-first stack:** FastAPI + async SQLAlchemy (asyncpg) + PostgreSQL behind **PgBouncer**
(transaction pooling), a **transactional outbox** (PostgreSQL `LISTEN/NOTIFY` + 5 s fallback poll +
`FOR UPDATE SKIP LOCKED`) whose relay publishes plain-JSON events to **RabbitMQ**, consumed by thin
**async aio-pika** workers. Observability via **Prometheus + Grafana**. The whole system runs with one
`docker compose up`.

> Full design rationale, diagrams and measured results: **[`docs/report.pdf`](docs/report.pdf)**.

## Prerequisites

- Docker + Docker Compose
- Free ports: `8000` (API), `5432` (PostgreSQL), `6432` (PgBouncer), `5672`/`15672`/`15692`
  (RabbitMQ + management + metrics), `9090` (Prometheus), `3000` (Grafana)
- Python 3.11+ — only to run the test suite / load generator locally

## Run the stack

```bash
docker compose up --build -d
```

Ten services start; a one-shot `migrate` service applies `alembic upgrade head` before the API,
outbox relay and workers come up (they wait for it). Check the API is healthy:

```bash
curl -s http://localhost:8000/health        # {"status":"healthy", ...}
```

- API docs (Swagger UI): <http://localhost:8000/docs>
- Grafana (anonymous view of the ingestion dashboard, no login): <http://localhost:3000> — admin login `admin` / `admin`
- Prometheus: <http://localhost:9090> · RabbitMQ management UI: <http://localhost:15672> (guest / guest)

Tear down (and wipe volumes):

```bash
docker compose down -v
```

## Smoke test — register → ingest → alert

```bash
# 1. register a device (operator action -> needs X-Admin-Token). The response contains an
#    api_key scoped to (device_id, patient_id).
curl -s -X POST http://localhost:8000/devices/register \
  -H 'X-Admin-Token: change-me-admin-token' -H 'Content-Type: application/json' \
  -d '{"device_id":"HR001","patient_id":"patient_001","device_type":"heart_rate"}'

# 2. ingest a clinically high reading (heart_rate > 150 -> triggers an alert).
#    Put the api_key from step 1 into the X-Device-Key header.
curl -s -X POST http://localhost:8000/ingest/ \
  -H "X-Device-Key: <API_KEY>" -H 'Content-Type: application/json' \
  -d '{"device_id":"HR001","patient_id":"patient_001",
       "timestamp":"2026-01-01T12:00:00Z",
       "device_type":"heart_rate","heart_rate":160}'              # -> 201 Created

# 3. after ~1 s the alert has been stored via outbox -> relay -> RabbitMQ -> consumer
docker exec medical_data_db psql -U user -d medical_data \
  -c "SELECT field, value, rule, severity FROM alerts;"
```

Re-sending the same `(device_id, timestamp)` returns `200` (idempotent, no duplicate row);
an out-of-range value returns `422`; a missing `X-Device-Key` returns `401`.

Other endpoints (full reference at `/docs`). The patient reads
`GET /aggregations/{patient_id}`, `POST /patients/{id}/risk-score` and
`GET /patients/{id}/risk-scores` are **patient-scoped** — send an `X-Device-Key` whose
patient matches the path (missing/invalid → `401`, wrong patient → `403`). The dead-letter
admin `GET /admin/failed-jobs` and `POST /admin/failed-jobs/{id}/replay` need the operator
`X-Admin-Token`.

## Tests

The suite runs against the **live stack** (API on `:8000`, PostgreSQL on `:5432`), so start the stack
first, then:

```bash
pip install -r requirements.txt        # or: poetry install
pytest
```

The reliability/chaos and burst-drain tests are opt-in (the chaos ones kill/restart containers):

```bash
RUN_CHAOS=1 pytest tests/test_reliability.py
RUN_LOAD=1  pytest tests/test_load_drain.py
```

## Load test & metrics

```bash
# drive load (registers devices, hammers /ingest, ~5% risk-score requests)
python -m locust -f scripts/locustfile.py --host http://localhost:8000 \
  --users 150 --spawn-rate 30 --run-time 90s --headless --csv out

# optional: render PNG charts from Prometheus over the load window (needs: pip install matplotlib)
python scripts/render_metrics.py
```

Watch it live in Grafana (<http://localhost:3000>): ingest RPS, status codes, latency percentiles, and
RabbitMQ queue depth.

## Documentation

- [`docs/report.pdf`](docs/report.pdf) — architecture, decision narrative, diagrams (container / ER / sequence), and measured results
