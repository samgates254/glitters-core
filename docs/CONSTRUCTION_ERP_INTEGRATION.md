# Construction ERP Integration

The construction domain is isolated in `apps/construction/`. It does not replace
identity, organization, property, tenancy, spatial, operations, marketplace, or
solutions records.

## Separate integration commit

After the construction migration has been reviewed, add the app and mount:

```python
# config/settings/base.py
INSTALLED_APPS = [
    # existing apps...
    "apps.construction.apps.ConstructionConfig",
]
```

```python
# config/urls.py
urlpatterns = [
    # existing paths...
    path("api/v1/construction/", include("apps.construction.urls")),
]
```

Apply migrations only in an environment with a dedicated database:

```bash
python manage.py migrate construction
```

Do not apply these migrations to the long-lived development database during
isolated feature development.

## Authorization

Every service requires active organization access. Organization owners,
administrators, and managers receive project-administrator authority.
Construction-specific roles are stored on `ProjectMember` and may be held in
multiple combinations.

Technical-document downloads are organization-scoped and reject non-member
users. Storage files use Django's configured storage abstraction and never
accept a filesystem path supplied by an API client.

## Integration contracts

The application references:

- `organizations.Organization`
- `properties.Property`
- `settings.AUTH_USER_MODEL`

It does not create competing identity, organization, or property models and does
not grant property authority or legal ownership.
