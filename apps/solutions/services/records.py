"""Projects, insurance, investments, and professional services.

Staff publish these records. Nothing here fabricates a premium or a market price.
"""

from __future__ import annotations

from django.db import IntegrityError

from apps.solutions.domain import (
    CREDENTIAL_CLAIM,
    INVALID,
    NOT_FOUND,
    PUBLISH_BLOCKED,
    CatalogueStatus,
    InsuranceCategory,
    InsuranceLine,
    InvestmentCategory,
    ProjectCategory,
    ProjectStatus,
    QuotationMode,
    contains_unchecked_credential_claim,
    new_reference,
)
from apps.solutions.errors import SolutionsError
from apps.solutions.models import (
    DevelopmentProject,
    InsuranceProduct,
    InvestmentOpportunity,
    ServiceCategory,
    ServiceProvider,
)
from apps.solutions.services.access import ensure_staff
from apps.solutions.services.fields import (
    active_organization,
    choice,
    flag,
    optional_date,
    optional_datetime,
    optional_email,
    optional_https,
    optional_money,
    optional_phone,
    reject_unknown,
    require_mapping,
    slug,
    text,
)

PROJECT_FIELDS = frozenset(
    {
        "slug",
        "name",
        "developer_name",
        "developer_id",
        "location_label",
        "description",
        "category",
        "status",
        "expected_completion",
        "units_summary",
        "pricing_note",
        "brochure_url",
        "contact_name",
        "contact_email",
        "contact_phone",
    }
)
INSURANCE_FIELDS = frozenset(
    {
        "slug",
        "name",
        "line",
        "category",
        "summary",
        "quotation_mode",
        "api_configured",
        "partner_id",
        "partner_name",
        "status",
    }
)
INVESTMENT_FIELDS = frozenset(
    {
        "name",
        "symbol",
        "category",
        "summary",
        "market_note",
        "price_minor",
        "data_source",
        "price_as_of",
        "risk_disclosure",
        "partner_id",
        "partner_name",
        "status",
    }
)
PROVIDER_FIELDS = frozenset(
    {
        "category_slug",
        "organization_id",
        "name",
        "coverage",
        "summary",
        "credentials_note",
        "credentials_checked",
        "portfolio_note",
        "contact_name",
        "contact_email",
        "contact_phone",
        "status",
    }
)


def _project_values(fields: object) -> dict:
    body = require_mapping(fields)
    reject_unknown(body, PROJECT_FIELDS)
    status = choice(body.get("status", ProjectStatus.DRAFT), ProjectStatus.VALUES)
    values = {
        "slug": slug(body.get("slug")),
        "name": text(body.get("name"), maximum=200, required=True),
        "developer_name": text(body.get("developer_name", ""), maximum=200),
        "developer": active_organization(body.get("developer_id")),
        "location_label": text(body.get("location_label"), maximum=200, required=True),
        "description": text(body.get("description"), maximum=8000, required=True),
        "category": choice(body.get("category"), ProjectCategory.VALUES),
        "status": status,
        "expected_completion": optional_date(body.get("expected_completion")),
        "units_summary": text(body.get("units_summary", ""), maximum=300),
        "pricing_note": text(body.get("pricing_note", ""), maximum=300),
        "brochure_url": optional_https(body.get("brochure_url", "")),
        "contact_name": text(body.get("contact_name", ""), maximum=160),
        "contact_email": optional_email(body.get("contact_email", "")),
        "contact_phone": optional_phone(body.get("contact_phone", "")),
    }
    if status != ProjectStatus.DRAFT and not (values["developer_name"] or values["contact_email"]):
        raise SolutionsError(PUBLISH_BLOCKED, status_code=400)
    return values


def _insurance_values(fields: object) -> dict:
    body = require_mapping(fields)
    reject_unknown(body, INSURANCE_FIELDS)
    line = choice(body.get("line"), InsuranceLine.VALUES)
    category = choice(body.get("category"), InsuranceCategory.VALUES)
    if line == InsuranceLine.GENERAL and category not in InsuranceCategory.GENERAL:
        raise SolutionsError(INVALID, status_code=400)
    if line == InsuranceLine.LIFE and category not in InsuranceCategory.LIFE:
        raise SolutionsError(INVALID, status_code=400)
    status = choice(body.get("status", CatalogueStatus.DRAFT), CatalogueStatus.VALUES)
    partner_name = text(body.get("partner_name", ""), maximum=200)
    if status == CatalogueStatus.PUBLISHED and partner_name == "" and not body.get("partner_id"):
        raise SolutionsError(PUBLISH_BLOCKED, status_code=400)
    return {
        "slug": slug(body.get("slug")),
        "name": text(body.get("name"), maximum=200, required=True),
        "line": line,
        "category": category,
        "summary": text(body.get("summary"), maximum=4000, required=True),
        "quotation_mode": choice(
            body.get("quotation_mode", QuotationMode.REQUEST),
            QuotationMode.VALUES,
        ),
        "api_configured": flag(body.get("api_configured")),
        "partner": active_organization(body.get("partner_id")),
        "partner_name": partner_name,
        "status": status,
    }


def _investment_values(fields: object) -> dict:
    body = require_mapping(fields)
    reject_unknown(body, INVESTMENT_FIELDS)
    status = choice(body.get("status", CatalogueStatus.DRAFT), CatalogueStatus.VALUES)
    disclosure = text(body.get("risk_disclosure"), maximum=4000, required=True)
    if status == CatalogueStatus.PUBLISHED and disclosure == "":
        raise SolutionsError(PUBLISH_BLOCKED, status_code=400)
    return {
        "name": text(body.get("name"), maximum=200, required=True),
        "symbol": text(body.get("symbol", ""), maximum=32),
        "category": choice(body.get("category"), InvestmentCategory.VALUES),
        "summary": text(body.get("summary"), maximum=4000, required=True),
        "market_note": text(body.get("market_note", ""), maximum=4000),
        "price_minor": optional_money(body.get("price_minor")),
        "data_source": text(body.get("data_source", ""), maximum=200),
        "price_as_of": optional_datetime(body.get("price_as_of")),
        "risk_disclosure": disclosure,
        "partner": active_organization(body.get("partner_id")),
        "partner_name": text(body.get("partner_name", ""), maximum=200),
        "status": status,
    }


def _guard_credentials(values: dict) -> None:
    if values["credentials_checked"]:
        return
    combined = " ".join(
        [values["summary"], values["credentials_note"], values["portfolio_note"], values["name"]]
    )
    if contains_unchecked_credential_claim(combined):
        raise SolutionsError(CREDENTIAL_CLAIM, status_code=400)
    if values["status"] == CatalogueStatus.PUBLISHED and values["credentials_note"]:
        values["credentials_note"] = ""


def _provider_values(fields: object) -> dict:
    body = require_mapping(fields)
    reject_unknown(body, PROVIDER_FIELDS)
    category_slug = text(body.get("category_slug"), maximum=80)
    category = ServiceCategory.objects.filter(slug=category_slug).first()
    if category is None:
        raise SolutionsError(INVALID, status_code=400)
    values = {
        "category": category,
        "organization": active_organization(body.get("organization_id")),
        "name": text(body.get("name"), maximum=200, required=True),
        "coverage": text(body.get("coverage", ""), maximum=200),
        "summary": text(body.get("summary", ""), maximum=4000),
        "credentials_note": text(body.get("credentials_note", ""), maximum=300),
        "credentials_checked": flag(body.get("credentials_checked")),
        "portfolio_note": text(body.get("portfolio_note", ""), maximum=4000),
        "contact_name": text(body.get("contact_name", ""), maximum=160),
        "contact_email": optional_email(body.get("contact_email", "")),
        "contact_phone": optional_phone(body.get("contact_phone", "")),
        "status": choice(body.get("status", CatalogueStatus.DRAFT), CatalogueStatus.VALUES),
    }
    _guard_credentials(values)
    return values


def _save(instance, values: dict):
    for key, value in values.items():
        setattr(instance, key, value)
    instance.save()
    return instance


def create_project(*, actor, fields: object) -> DevelopmentProject:
    ensure_staff(actor)
    return DevelopmentProject.objects.create(**_project_values(fields))


def update_project(*, actor, slug_value: str, fields: object) -> DevelopmentProject:
    ensure_staff(actor)
    project = DevelopmentProject.objects.filter(slug=slug_value).first()
    if project is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    values = _project_values(fields)
    taken = DevelopmentProject.objects.filter(slug=values["slug"]).exclude(pk=project.pk).exists()
    if taken:
        raise SolutionsError(INVALID, status_code=400)
    return _save(project, values)


def create_insurance(*, actor, fields: object) -> InsuranceProduct:
    ensure_staff(actor)
    return InsuranceProduct.objects.create(**_insurance_values(fields))


def update_insurance(*, actor, slug_value: str, fields: object) -> InsuranceProduct:
    ensure_staff(actor)
    product = InsuranceProduct.objects.filter(slug=slug_value).first()
    if product is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    values = _insurance_values(fields)
    if InsuranceProduct.objects.filter(slug=values["slug"]).exclude(pk=product.pk).exists():
        raise SolutionsError(INVALID, status_code=400)
    return _save(product, values)


def create_investment(*, actor, fields: object) -> InvestmentOpportunity:
    ensure_staff(actor)
    values = _investment_values(fields)
    for _ in range(5):
        try:
            return InvestmentOpportunity.objects.create(reference=new_reference("INV"), **values)
        except IntegrityError:
            continue
    raise SolutionsError(INVALID, status_code=400)


def update_investment(*, actor, reference: str, fields: object) -> InvestmentOpportunity:
    ensure_staff(actor)
    row = InvestmentOpportunity.objects.filter(reference=reference).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return _save(row, _investment_values(fields))


def create_provider(*, actor, fields: object) -> ServiceProvider:
    ensure_staff(actor)
    return ServiceProvider.objects.create(**_provider_values(fields))


def update_provider(*, actor, provider_id, fields: object) -> ServiceProvider:
    ensure_staff(actor)
    row = ServiceProvider.objects.filter(pk=provider_id).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return _save(row, _provider_values(fields))


def public_projects():
    return DevelopmentProject.objects.filter(status__in=ProjectStatus.PUBLIC)


def public_project(slug_value: str) -> DevelopmentProject:
    row = public_projects().filter(slug=slug_value).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return row


def public_products():
    return InsuranceProduct.objects.filter(status=CatalogueStatus.PUBLISHED)


def public_product(slug_value: str) -> InsuranceProduct:
    row = public_products().filter(slug=slug_value).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return row


def public_investments():
    return InvestmentOpportunity.objects.filter(status=CatalogueStatus.PUBLISHED)


def public_investment(reference: str) -> InvestmentOpportunity:
    row = public_investments().filter(reference=reference).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return row


def public_categories():
    return ServiceCategory.objects.all()


def public_category(slug_value: str) -> ServiceCategory:
    row = public_categories().filter(slug=slug_value).first()
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return row


def public_provider(provider_id) -> ServiceProvider:
    row = (
        ServiceProvider.objects.select_related("category")
        .filter(pk=provider_id, status=CatalogueStatus.PUBLISHED)
        .first()
    )
    if row is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return row
