# System architecture

Status: foundation, identity authentication, organization RBAC, the property and location domain, and the marketplace listing engine are implemented. Payment behaviour is design, not code.

## Repository condition at creation

No Glitters backend repository existed under the home directory. The files in `~/Downloads` named `Glitters_Executive_Presentation` and `Glitters_Real_Estate_Digital_Ecosystem_Proposal` are documents, not an application. This repository was created at `/home/mr-robot/Projects/glitters-core`.

## Shape

GLITTERS CORE is a modular monolith. One deployable Django process owns the transactional rules. Domain code will live in separate Django apps with explicit service boundaries so a later extraction is possible. Microservices are not introduced in advance of an independent operational need.

```text
Public marketplace
Authenticated business application
GLITTERS CONTROL CENTER
            |
            v
      /api/v1/  (DRF, OpenAPI)
            |
            v
   Application services and selectors
            |
            v
   PostgreSQL + PostGIS   Redis    Celery
```

Three experiences share this API. They do not each own a database.

## Layers

| Layer | Responsibility | Foundation location |
|---|---|---|
| Presentation | HTTP, serializers, status codes, schema | `apps/core/views.py`, `apps/identity/views.py`, `apps/organizations/views.py`, `apps/locations/views.py`, `apps/properties/views.py`, `apps/marketplace/views.py`, `config/api.py` |
| Application | Use-case coordination | `apps/core/services/`, `apps/identity/services/`, `apps/organizations/services/`, `apps/locations/services/`, `apps/properties/services/`, `apps/marketplace/services/` |
| Domain | Rules and invariants | `apps/identity/domain.py`, `apps/organizations/domain.py`, `apps/locations/domain.py`, `apps/properties/domain.py`, `apps/marketplace/domain.py`, and the model modules |
| Data access | Constraints, migrations, queries | Django ORM |
| Infrastructure | Database, cache, broker, containers, logs | `config/settings/`, `compose.yaml`, `infrastructure/` |

Views stay thin. Serializers do not own business rules. There is no generic repository framework.

## Request path

1. Nginx or the development server accepts the connection.
2. `RequestIdMiddleware` assigns or accepts `X-Request-ID`.
3. Django security, session, CSRF, and authentication middleware run.
4. DRF authenticates and authorizes. The default permission is authenticated.
5. A view calls a service. The service uses the ORM inside a transaction when a write needs one.
6. Logs include the request id. JSON logging is available for staging and production.

Sessions are stored in PostgreSQL so an application instance can disappear without taking login state with it. Cache and broker state live in Redis. Financial and booking truth will not live only in Redis.

## Runtime processes

| Process | Role | State in this milestone |
|---|---|---|
| Django WSGI/ASGI | HTTP API | ASGI application is configured. Host verification uses `runserver`. |
| Celery worker | Background work | Boots against Redis and can execute a task. No application tasks are registered. |
| Celery beat | Schedule | Process starts. The schedule is empty. No database-backed scheduler yet. |
| PostgreSQL/PostGIS | System of record | Development container, verified. |
| Redis | Cache and broker | Development container, verified. |
| Nginx | Reverse proxy | Reference configuration. Not required to run the API. |

Channels is intentionally absent. Real-time delivery is a later milestone and will use a Redis channel layer so sticky sessions are not required for chat fan-out.

## Horizontal scaling

The application does not keep essential state only in process memory. More than one API process can run behind a load balancer. Health probes use `/api/v1/health/live/` for liveness and `/api/v1/health/ready/` for readiness. Graceful process shutdown is provided by Gunicorn in the container command. Autoscaling, read replicas, and a managed load balancer are not deployed.

```text
Clients
  |
DNS / CDN / WAF          (not deployed)
  |
Load balancer            (not deployed)
  |
Nginx                    (reference config)
  |
Django instances
  |
PostgreSQL    Redis    Celery
```

## Environments

| Environment | Settings module | Database |
|---|---|---|
| Development | `config.settings.development` | Local PostGIS container |
| Staging | `config.settings.staging` | Separate, not provisioned |
| Production | `config.settings.production` | Separate, not provisioned |

Development reads `.env`. Staging and production ignore that file and refuse `DEBUG=true`, a placeholder secret, and `ALLOWED_HOSTS=*`.

## Versioning

The HTTP namespace is `/api/v1/`. Response bodies for health are flat operational documents. DRF errors use `{ "errors": ..., "request_id": ... }`. Business resources, when they exist, will have their own serializers rather than exposing Django model internals.

## Observability

Logs are structured when `DJANGO_JSON_LOGS=true`. Each record can carry `request_id`. Error tracking, metrics, tracing, and uptime monitors are not connected. A backup has not been taken, and a restore has not been tested. Proposed recovery targets are recorded as targets in the infrastructure document and are not operational commitments.

## M1 freeze

Verified on 2026-11-10 against the development containers. This section records evidence, not a production launch.

- PostgreSQL 16.9 on `127.0.0.1:5434` accepts connections as the `glitters` database user. PostGIS 3.5.2 is installed. `PostGIS_Version()` returns `3.5 USE_GEOS=1 USE_PROJ=1 USE_STATS=1`. A transaction was committed and a second transaction was rolled back.
- Redis 7.4 on `127.0.0.1:6381` answers on logical databases 0, 1, and 2.
- A Celery 5.7 worker started with prefork and again with the solo pool. `celery.accumulate` returned `SUCCESS` and the value was read back from the result backend. The same call stayed `PENDING` and timed out when no worker was running. The worker advertises no application tasks. Retry behavior that is configured is broker retry on startup, late acknowledgement, and reject-on-worker-lost. There is no per-task autoretry policy.
- Celery beat starts with `PersistentScheduler`. `CELERY_BEAT_SCHEDULE` is empty, so no job runs.
- `GET /api/v1/health/live/`, `GET /api/v1/health/ready/`, `GET /api/v1/schema/?format=json`, and `GET /api/v1/docs/` returned 200. `POST /api/v1/health/live/` returned 405 with `{errors, request_id}`. `GET /api/v1/` has no route and returns 404. CORS allows `http://localhost:3000` and does not echo any other origin. Development responses send `X-Content-Type-Options`, `X-Frame-Options: DENY`, and `Referrer-Policy`. HSTS and secure cookies belong to the staging and production settings modules.
- `identity.User` is migrated. Public tables that reference it are `identity_user_groups`, `identity_user_user_permissions`, and `django_admin_log`. There is no `auth_user` table and no business model.

M2 adds authentication on this user model. It does not replace `AUTH_USER_MODEL`. Phone, sessions, mobile credentials, MFA, and the account lifecycle are specified in [IDENTITY_ARCHITECTURE.md](IDENTITY_ARCHITECTURE.md). Celery still has no application tasks, and the beat schedule is still empty.

M3 adds organizations, memberships, invitations, and organization-scoped roles in the same database. The isolation rules are in [MULTI_TENANCY_ARCHITECTURE.md](MULTI_TENANCY_ARCHITECTURE.md). Celery still has no application tasks.

M4 adds countries, administrative areas, localities, addresses, properties, characteristics, parties, and property access in the same database. PostGIS stores an optional geography point. The domain is in [PROPERTY_ARCHITECTURE.md](PROPERTY_ARCHITECTURE.md) and [LOCATION_ARCHITECTURE.md](LOCATION_ARCHITECTURE.md). Celery still has no application tasks. Listings and payments are still not implemented.
