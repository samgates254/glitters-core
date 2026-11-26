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

## Stage C quantity surveying and cost control

The app's authenticated, project-scoped API is mounted at
`/api/v1/construction/` by the separate integration change above. Every
resource lookup is scoped to the project and its organization; write operations
also enforce the relevant construction role and workflow state.

| Method | Path | Purpose |
|---|---|---|
| `GET`, `POST` | `projects/{project_id}/boqs/` | List or create a BoQ |
| `GET`, `POST` | `projects/{project_id}/boqs/{boq_id}/sections/` | List or add BoQ sections and subsections |
| `GET`, `POST` | `projects/{project_id}/boqs/{boq_id}/sections/{section_id}/items/` | List or add measured work items |
| `PATCH`, `DELETE` | `projects/{project_id}/boqs/{boq_id}/sections/{section_id}/items/{item_id}/` | Change draft work items only |
| `POST` | `projects/{project_id}/boqs/{boq_id}/submit/`, `/review/`, `/approve/` | Run the controlled BoQ approval workflow |
| `POST` | `projects/{project_id}/boqs/{boq_id}/revisions/` | Create a new revision without changing historical content |
| `GET` | `projects/{project_id}/boqs/{boq_id}/export/?output=csv` or `?output=xlsx` | Export a BoQ |
| `GET`, `POST` | `projects/{project_id}/budgets/` | List or create cost plans |
| `GET`, `POST` | `projects/{project_id}/budgets/{budget_id}/categories/` | Read or define planned budget categories |
| `POST` | `projects/{project_id}/budgets/{budget_id}/submit/`, `/approve/`, `/revise/` | Submit, approve, or revise a cost plan |
| `GET`, `POST` | `projects/{project_id}/budgets/{budget_id}/costs/` | Read or record commitments, actuals, and forecasts |
| `GET` | `projects/{project_id}/cost-report/` | Return approved budget, commitments, actuals, forecasts, and variations |
| `GET` | `projects/{project_id}/cost-report/?output=csv` or `?output=xlsx` | Export project cost reporting |
| `GET`, `POST` | `projects/{project_id}/variations/` | List or request a variation |
| `POST` | `projects/{project_id}/variations/{variation_id}/review/`, `/approve/`, `/reject/` | Review, approve, or reject a requested variation |

BoQ quantities and rates, budget amounts, cost records, and variation impacts
use decimal values. Approved BoQs, approved budgets, and recorded cost rows are
protected from direct changes. PDF export is not provided; requesting it
returns `406` until a PDF renderer is selected.

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
