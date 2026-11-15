# Infrastructure architecture

Status: development data services are runnable and were verified on the engineering workstation. Staging, production, backups, and external monitoring are not operational.

## Development topology

```text
Host Python 3.14 virtualenv
    Django runserver 127.0.0.1:8010
    GDAL 3.12.2 and GEOS 3.14.1 are loaded from the host
            |                |
            v                v
glitters-postgres      glitters-redis
127.0.0.1:5434         127.0.0.1:6381
postgis/postgis:16-3.5 redis:7-alpine
```

Compose project name: `glitters-core`.

These ports were chosen so the project does not attach to:

| Port | Already used by |
|---|---|
| 5432 | Host PostgreSQL 18 |
| 5433 | `avestellar-postgres` (`postgis/postgis:16-3.5`) |
| 6379 | `avestellar-redis` |
| 6380 | `avestellar_dev_redis` |
| 8000 | An existing Python process |

## Containers

| Service | Image | Profile | Purpose |
|---|---|---|---|
| `postgres` | `postgis/postgis:16-3.5` | default | Development system of record |
| `redis` | `redis:7-alpine` | default | Cache, broker, result backend |
| `api` | Local `Dockerfile` | `app` | Optional API container |
| `worker` | Local `Dockerfile` | `app` | Optional Celery worker |
| `beat` | Local `Dockerfile` | `app` | Optional Celery beat |
| `nginx` | `nginx:1.28-alpine` | `proxy` | Optional reverse proxy on `127.0.0.1:8088` |

The API, worker, beat, and nginx services are prepared. They are not the path used to verify the first health check. Host `uv` plus the two data containers is the development path.

The image installs GDAL, GEOS, and PROJ so GeoDjango can run inside the container. It runs as uid 10001. Migrations run on container start only when `GLITTERS_AUTO_MIGRATE=true`, which the development `api` service sets. Production should run migrations as an explicit step.

## Redis databases

| Index | Use |
|---|---|
| 0 | Django cache |
| 1 | Celery broker |
| 2 | Celery results |

Redis is not authoritative for money or reservations.

## Configuration

| Module | Reads `.env` | Debug |
|---|---|---|
| `config.settings.development` | Yes, without overriding existing variables | Allowed |
| `config.settings.staging` | No | Refused |
| `config.settings.production` | No | Refused |

`DATABASE_CONN_MAX_AGE` defaults to 60 seconds. If PgBouncer is introduced in front of PostgreSQL, set this to 0. PgBouncer is not deployed. Read replicas are not deployed and should wait until queries have a deliberate read/write split.

## CI

`.github/workflows/ci.yml` runs, on GitHub-hosted runners:

- Ruff lint and format check
- Migration drift check
- Django system check
- Pytest with coverage
- `pip-audit`

CI brings its own PostGIS 16 and Redis 7. It does not deploy.

## Load balancing

Nginx is configured with an upstream, forwarded protocol, request ids, a 60 second proxy read timeout, and a shorter timeout on the liveness path. Sticky sessions are not required for the current HTTP API. A managed cloud load balancer can replace this file without an application change, provided it sends `X-Forwarded-Proto` and does not buffer health checks indefinitely.

No load balancer is running for GLITTERS CORE.

## Backups and recovery

No backup job is configured. A backup is not reliable until a restore has been executed and checked.

Proposed targets, not yet accepted as an operational commitment:

| Target | Proposal |
|---|---|
| Recovery point | 15 minutes once point-in-time recovery is available |
| Recovery time | 4 hours for the transactional database |
| Retention | 30 days of daily backups plus the PITR window |

Those numbers are planning placeholders. They are not evidence of a tested restore.

## Monitoring

The process can emit JSON logs and answer health probes. The following are not connected: error tracking, metrics, slow-query collection, queue alerts, uptime checks, and a status page.

## Production constraints

- Production does not share the development database, Redis, or secret.
- The development password in `compose.yaml` is local only.
- Object storage, email delivery, SMS, maps, and M-Pesa are not configured.
- The container build is a development artifact until it has been built and run in a clean environment and promoted by the CI pipeline.
