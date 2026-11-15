# GLITTERS CORE

Backend for **GLITTERS**, the real estate platform of **GLITTERS EQUITY INVESTMENT SOLUTIONS**.

This repository is the modular-monolith API. M1 is the engineering foundation. M2 adds identity and authentication on `identity.User`. M3 adds organizations, memberships, invitations, and organization-scoped roles. M4 adds the canonical property and location domain. Listings and payments are specified and not implemented.

## Supported versions

| Component | Version |
|---|---|
| Python | 3.14 |
| Django | 5.2 LTS |
| PostgreSQL | 16, with PostGIS 3.5, in development |
| Redis | 7 |
| API | `/api/v1/` |

The workstation PostgreSQL 18 server is not the application database. It does not have PostGIS, and other projects already use it.

## What runs today

- `GET /api/v1/health/live/` confirms the process is up.
- `GET /api/v1/health/ready/` checks PostgreSQL, PostGIS, and Redis.
- OpenAPI schema at `GET /api/v1/schema/` and Swagger UI at `GET /api/v1/docs/`.
- Email is the user identifier. Passwords are hashed with Argon2.
- Registration, login, logout, email verification, password reset, phone verification, and TOTP are under `/api/v1/auth/`.
- Browser clients use an HTTP-only session cookie. Mobile clients use opaque access and refresh tokens.
- Django admin at `/admin/` is an operator bootstrap, not GLITTERS CONTROL CENTER. It does not enforce TOTP. The API does, for staff.

## What is not built

Listings, payments, M-Pesa, messaging, Channels, and production hosting are later milestones. Configuration files do not mean those services are operational. Phone codes are emailed. There is no SMS provider. A registration reference on an organization is not legal verification. A property party is not legal title.

The M1 foundation was verified on 2026-11-10 at `a1ab779`. `identity.User` stays `AUTH_USER_MODEL`. M2 is documented in `docs/architecture/IDENTITY_ARCHITECTURE.md` and `docs/engineering/M2_IDENTITY_IMPLEMENTATION.md`. M3 is documented in `docs/architecture/ORGANIZATION_ARCHITECTURE.md` and `docs/engineering/M3_IMPLEMENTATION.md`. M4 is documented in `docs/architecture/PROPERTY_ARCHITECTURE.md`, `docs/architecture/LOCATION_ARCHITECTURE.md`, and `docs/engineering/M4_IMPLEMENTATION.md`. Celery has no application tasks, and the beat schedule is empty.

## Local setup

Requirements: Python 3.14, [uv](https://docs.astral.sh/uv/), and Docker.

```bash
cd ~/Projects/glitters-core
uv sync --frozen
cp .env.example .env
# Replace DJANGO_SECRET_KEY with a long random value.
docker compose up -d --wait postgres redis
uv run python manage.py migrate
uv run python manage.py check
uv run pytest
```

Activate the environment:

```bash
source ~/Projects/glitters-core/.venv/bin/activate
```

Start the development server. Port 8010 is the project port because 8000 was already in use on the workstation where this repository was created.

```bash
python manage.py runserver 127.0.0.1:8010
```

Health check:

```bash
./infrastructure/scripts/check-health.sh http://127.0.0.1:8010
```

Stop the data services:

```bash
docker compose stop postgres redis
```

The optional application containers are not required for host development:

```bash
docker compose --profile app up --build api worker beat
```

Nginx is a reference reverse proxy and is not part of the default stack:

```bash
docker compose --profile app --profile proxy up --build
```

## Development ports

| Service | Address | Reason |
|---|---|---|
| API | `127.0.0.1:8010` | Host port 8000 is already taken |
| PostgreSQL/PostGIS | `127.0.0.1:5434` | Host 5432 and Avestellar 5433 are already taken |
| Redis | `127.0.0.1:6381` | 6379 and 6380 are already taken |
| Nginx profile | `127.0.0.1:8088` | Published only with the proxy profile |

The database password in `compose.yaml` is a local development credential bound to localhost. It is not a production secret. Production must supply its own credentials through the process environment.

## GeoDjango

PostGIS is installed in the development database. On this workstation `libgdal.so.38` (GDAL 3.12.2) and `libgeos_c.so.1` (GEOS 3.14.1) are already present, so Django uses `django.contrib.gis.db.backends.postgis`. The `gdal-bin` package is not required for that. Django loads the shared library directly.

A machine without those libraries falls back to the PostgreSQL backend, reports system check `glitters.W001`, and still verifies PostGIS with SQL. Install the libraries there with:

```bash
sudo apt install gdal-bin libgdal-dev libgeos-dev libproj-dev binutils
```

The API image installs the same libraries. Building the image does not mean a production environment is running.

## Tests and checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest
uv run pip-audit
```

## Further reading

- [System architecture](docs/architecture/SYSTEM_ARCHITECTURE.md)
- [Domain model](docs/architecture/DOMAIN_MODEL.md)
- [Security architecture](docs/architecture/SECURITY_ARCHITECTURE.md)
- [Infrastructure architecture](docs/architecture/INFRASTRUCTURE_ARCHITECTURE.md)
- [Decisions](docs/decisions/README.md)
- [Local operations](docs/operations/LOCAL_DEVELOPMENT.md)
- [API surface](docs/api/V1.md)

Proprietary software of GLITTERS EQUITY INVESTMENT SOLUTIONS. All rights reserved.
