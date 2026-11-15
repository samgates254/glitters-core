# Local development

## First run

```bash
cd ~/Projects/glitters-core
uv sync --frozen
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the printed value in `.env` as `DJANGO_SECRET_KEY`.

```bash
docker compose up -d --wait postgres redis
uv run python manage.py migrate
uv run python manage.py check
uv run pytest
```

## Everyday commands

Activate the environment:

```bash
source ~/Projects/glitters-core/.venv/bin/activate
```

Start the API:

```bash
python manage.py runserver 127.0.0.1:8010
```

Check health:

```bash
./infrastructure/scripts/check-health.sh http://127.0.0.1:8010
```

Create an operator for Django admin:

```bash
python manage.py createsuperuser
```

Stop data services:

```bash
docker compose stop postgres redis
```

Remove the development database volume only when the data can be discarded:

```bash
docker compose down
```

`docker compose down -v` deletes the PostGIS volume. Do not run it unless that data loss is intended.

## GeoDjango on the host

This workstation already loads GDAL 3.12.2 and GEOS 3.14.1, so `python manage.py check` is clean and the database engine is PostGIS. Another machine enables the same engine when `libgdal` is visible or `GDAL_LIBRARY_PATH` points at it. Otherwise Django reports `glitters.W001` and uses the PostgreSQL backend while the readiness query still calls `PostGIS_Version()`.

```bash
sudo apt install gdal-bin libgdal-dev libgeos-dev libproj-dev binutils
```

## Optional containers

```bash
docker compose --profile app up --build api worker beat
docker compose --profile app --profile proxy up --build
```

The image defaults to production settings and will refuse to boot without a real secret and `DJANGO_DEBUG=false`. The Compose `app` profile overrides the settings module to development.

## CI equivalents

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest --cov=apps --cov=config --cov-report=term-missing
uv run pip-audit
```
