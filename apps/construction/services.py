from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.utils import timezone

from apps.construction.domain import (
    MONEY_QUANTUM,
    PROJECT_TRANSITIONS,
    BoQStatus,
    BudgetStatus,
    DefectStatus,
    InspectionStatus,
    ProjectRole,
    ProjectStatus,
    RFIStatus,
    TenderStatus,
    VariationStatus,
)
from apps.construction.errors import ConstructionError
from apps.construction.models import (
    ApprovedVariation,
    AuditEvent,
    BoQ,
    BoQItem,
    BoQSection,
    BudgetCategory,
    ConstructionProject,
    CostRecord,
    Defect,
    EngineeringInspection,
    EngineeringIssue,
    ProjectActivity,
    ProjectBudget,
    ProjectMember,
    ProjectMilestone,
    SiteDiary,
    TenderBid,
)
from apps.construction.permissions import PROJECT_MANAGEMENT_ROLES, has_org_access, has_project_role


def _require_org(user, organization):
    if not has_org_access(user, organization):
        raise ConstructionError("You do not have access to this organization.", status_code=403)


def _require_project_role(user, project, roles):
    if not has_project_role(user, project, roles):
        raise ConstructionError(
            "You do not have permission for this project action.", status_code=403
        )


def audit(project, actor, action, entity_type, entity_id=None, **metadata):
    AuditEvent.objects.create(
        project=project,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )


@transaction.atomic
def create_project(
    *,
    actor,
    organization,
    code,
    name,
    classification,
    description="",
    property=None,
    budget=Decimal("0"),
    start_date=None,
    expected_completion_date=None,
    project_manager=None,
):
    _require_org(actor, organization)
    if budget < 0:
        raise ConstructionError("Budget cannot be negative.")
    project = ConstructionProject.objects.create(
        organization=organization,
        property=property,
        code=code.strip().upper(),
        name=name,
        classification=classification,
        description=description,
        budget=budget,
        start_date=start_date,
        expected_completion_date=expected_completion_date,
        project_manager=project_manager,
        created_by=actor,
    )
    audit(project, actor, "project.created", "project", project.id)
    return project


@transaction.atomic
def transition_project(*, actor, project, target_status):
    project = ConstructionProject.objects.select_for_update().get(pk=project.pk)
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    allowed = PROJECT_TRANSITIONS.get(project.status, frozenset())
    if target_status not in allowed:
        raise ConstructionError(
            f"Project transition {project.status} -> {target_status} is not allowed."
        )
    if (
        target_status in (ProjectStatus.APPROVED, ProjectStatus.COMPLETED)
        and not project.project_manager
    ):
        raise ConstructionError("A project manager is required before approval or completion.")
    previous_status = project.status
    project.status = target_status
    project.version += 1
    project.save(update_fields=["status", "version", "updated_at"])
    audit(
        project,
        actor,
        "project.status_changed",
        "project",
        project.id,
        from_status=previous_status,
        to_status=target_status,
    )
    return project


@transaction.atomic
def add_project_member(*, actor, project, user, role):
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if role not in ProjectRole.VALUES:
        raise ConstructionError("Unknown construction project role.")
    member = ProjectMember.objects.create(project=project, user=user, role=role, added_by=actor)
    audit(project, actor, "project_member.added", "project_member", member.id, role=role)
    return member


@transaction.atomic
def create_inspection(
    *, actor, project, title, inspector, inspection_date, observations="", safety_observations=""
):
    _require_project_role(
        actor,
        project,
        {ProjectRole.PROJECT_MANAGER, ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR},
    )
    inspection = EngineeringInspection.objects.create(
        project=project,
        title=title,
        inspector=inspector,
        inspection_date=inspection_date,
        observations=observations,
        safety_observations=safety_observations,
    )
    audit(project, actor, "inspection.created", "inspection", inspection.id)
    return inspection


@transaction.atomic
def submit_inspection(*, actor, inspection):
    project = inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    if inspection.status != InspectionStatus.DRAFT:
        raise ConstructionError("Only draft inspections can be submitted.")
    inspection.status = InspectionStatus.SUBMITTED
    inspection.submitted_at = timezone.now()
    inspection.save(update_fields=["status", "submitted_at"])
    audit(project, actor, "inspection.submitted", "inspection", inspection.id)
    return inspection


@transaction.atomic
def record_defect(
    *, actor, inspection, reference, description, rectification_order="", assigned_to=None
):
    project = inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    defect = Defect.objects.create(
        inspection=inspection,
        reference=reference,
        description=description,
        rectification_order=rectification_order,
        assigned_to=assigned_to,
        status=DefectStatus.ASSIGNED if assigned_to else DefectStatus.OPEN,
    )
    audit(project, actor, "defect.recorded", "defect", defect.id)
    return defect


@transaction.atomic
def close_defect(*, actor, defect):
    project = defect.inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    if defect.status not in (DefectStatus.RECTIFIED, DefectStatus.ASSIGNED, DefectStatus.OPEN):
        raise ConstructionError("Defect cannot be closed in its current state.")
    defect.status = DefectStatus.CLOSED
    defect.closed_by = actor
    defect.closed_at = timezone.now()
    defect.save(update_fields=["status", "closed_by", "closed_at"])
    audit(project, actor, "defect.closed", "defect", defect.id)
    return defect


@transaction.atomic
def create_site_diary(*, actor, project, diary_date, **fields):
    _require_project_role(
        actor,
        project,
        {
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.CIVIL_ENGINEER,
            ProjectRole.SITE_INSPECTOR,
            ProjectRole.CONTRACTOR,
        },
    )
    diary = SiteDiary.objects.create(
        project=project,
        author=actor,
        diary_date=diary_date,
        **fields,
    )
    audit(project, actor, "site_diary.created", "site_diary", diary.id)
    return diary


@transaction.atomic
def approve_site_diary(*, actor, diary):
    _require_project_role(
        actor,
        diary.project,
        {ProjectRole.PROJECT_MANAGER, ProjectRole.CIVIL_ENGINEER},
    )
    diary.approved_by = actor
    diary.approved_at = timezone.now()
    diary.save(update_fields=["approved_by", "approved_at", "updated_at"])
    audit(diary.project, actor, "site_diary.approved", "site_diary", diary.id)
    return diary


@transaction.atomic
def record_engineering_issue(
    *,
    actor,
    project,
    reference,
    category,
    severity,
    description,
    responsible_party=None,
    resolution_deadline=None,
):
    _require_project_role(
        actor,
        project,
        {
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.CIVIL_ENGINEER,
            ProjectRole.STRUCTURAL_ENGINEER,
            ProjectRole.SITE_INSPECTOR,
        },
    )
    if severity not in ("low", "medium", "high", "critical"):
        raise ConstructionError("Unknown issue severity.")
    issue = EngineeringIssue.objects.create(
        project=project,
        reference=reference,
        category=category,
        severity=severity,
        description=description,
        responsible_party=responsible_party,
        resolution_deadline=resolution_deadline,
    )
    audit(project, actor, "engineering_issue.recorded", "engineering_issue", issue.id)
    return issue


@transaction.atomic
def close_engineering_issue(*, actor, issue, resolution_notes):
    _require_project_role(
        actor,
        issue.project,
        {
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.CIVIL_ENGINEER,
            ProjectRole.STRUCTURAL_ENGINEER,
            ProjectRole.SITE_INSPECTOR,
        },
    )
    issue.status = "closed"
    issue.resolution_notes = resolution_notes
    issue.closed_by = actor
    issue.closed_at = timezone.now()
    issue.save(update_fields=["status", "resolution_notes", "closed_by", "closed_at"])
    audit(issue.project, actor, "engineering_issue.closed", "engineering_issue", issue.id)
    return issue


def _decimal(value, label):
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ConstructionError(f"{label} must be a valid decimal number.") from exc
    if not result.is_finite():
        raise ConstructionError(f"{label} must be finite.")
    return result


def _money(value):
    try:
        result = value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except ArithmeticError as exc:
        raise ConstructionError("Calculated monetary amount exceeds supported precision.") from exc
    if result.copy_abs() >= Decimal("100000000000000"):
        raise ConstructionError("Calculated monetary amount exceeds supported precision.")
    return result


@transaction.atomic
def calculate_boq(boq):
    boq = BoQ.objects.select_for_update().get(pk=boq.pk)
    if boq.status in (BoQStatus.APPROVED, BoQStatus.SUPERSEDED):
        raise ConstructionError("Approved and superseded BoQs are locked.")
    subtotal = Decimal("0")
    for item in (
        BoQItem.objects.filter(section__boq=boq)
        .select_related("section")
        .order_by("section__sequence", "section__code", "item_code")
    ):
        quantity = _decimal(item.quantity, "Quantity")
        unit_rate = _decimal(item.unit_rate, "Unit rate")
        if quantity < 0 or unit_rate < 0:
            raise ConstructionError("Quantities and unit rates cannot be negative.")
        amount = _money(quantity * unit_rate)
        if item.amount != amount:
            item.amount = amount
            item.save(update_fields=["amount"])
        subtotal += amount
    subtotal = _money(subtotal)
    contingency_percent = _decimal(boq.contingency_percent, "Contingency")
    tax_percent = _decimal(boq.tax_percent, "Tax")
    if contingency_percent < 0 or tax_percent < 0:
        raise ConstructionError("Tax and contingency percentages cannot be negative.")
    contingency = _money(subtotal * contingency_percent / Decimal("100"))
    tax = _money((subtotal + contingency) * tax_percent / Decimal("100"))
    grand = _money(subtotal + contingency + tax)
    boq.subtotal = subtotal
    boq.contingency_amount = contingency
    boq.tax_amount = tax
    boq.grand_total = grand
    boq.save(update_fields=["subtotal", "contingency_amount", "tax_amount", "grand_total"])
    return subtotal, contingency, tax, grand


def boq_export_rows(boq):
    rows = [["Section", "Item code", "Description", "Unit", "Quantity", "Unit rate", "Amount"]]
    for section in boq.sections.order_by("sequence", "code"):
        rows.append([section.code, "", section.title, "", "", "", ""])
        for item in section.items.order_by("item_code"):
            rows.append(
                [
                    section.code,
                    item.item_code,
                    item.description,
                    item.unit,
                    item.quantity,
                    item.unit_rate,
                    item.amount,
                ]
            )
        section_total = section.items.aggregate(value=models.Sum("amount"))["value"] or Decimal("0")
        rows.append([section.code, "", "Section subtotal", "", "", "", section_total])
    rows.extend(
        [
            ["", "", "Subtotal", "", "", "", boq.subtotal],
            ["", "", "Contingency", "", "", boq.contingency_percent, boq.contingency_amount],
            ["", "", "Tax", "", "", boq.tax_percent, boq.tax_amount],
            ["", "", "Grand total", "", "", "", boq.grand_total],
        ]
    )
    return rows


def boq_csv_export(boq):
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        _csv_safe_row([f"{boq.reference} - {boq.title}", f"Revision {boq.revision}", boq.currency])
    )
    for row in boq_export_rows(boq):
        writer.writerow(_csv_safe_row(row))
    return output.getvalue()


def rows_xlsx_export(rows):
    import io
    import zipfile
    from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    root_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    register_namespace("", main_ns)
    register_namespace("r", rel_ns)

    def column_name(number):
        result = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result

    worksheet = Element(f"{{{main_ns}}}worksheet")
    sheet_data = SubElement(worksheet, f"{{{main_ns}}}sheetData")
    for row_number, values in enumerate(rows, start=1):
        row_element = SubElement(sheet_data, f"{{{main_ns}}}row", {"r": str(row_number)})
        for column_number, value in enumerate(values, start=1):
            cell = SubElement(
                row_element,
                f"{{{main_ns}}}c",
                {"r": f"{column_name(column_number)}{row_number}"},
            )
            text = str(value)
            if isinstance(value, (Decimal, int)) and not isinstance(value, bool):
                SubElement(cell, f"{{{main_ns}}}v").text = text
                continue
            cell.set("t", "inlineStr")
            inline = SubElement(cell, f"{{{main_ns}}}is")
            SubElement(inline, f"{{{main_ns}}}t").text = text

    workbook = Element(f"{{{main_ns}}}workbook")
    SubElement(workbook, f"{{{main_ns}}}sheets")
    SubElement(
        workbook.find(f"{{{main_ns}}}sheets"),
        f"{{{main_ns}}}sheet",
        {
            "name": "BoQ",
            "sheetId": "1",
            f"{{{rel_ns}}}id": "rId1",
        },
    )
    workbook_rels = Element(f"{{{package_rel_ns}}}Relationships")
    SubElement(
        workbook_rels,
        f"{{{package_rel_ns}}}Relationship",
        {
            "Id": "rId1",
            "Type": f"{rel_ns}/worksheet",
            "Target": "worksheets/sheet1.xml",
        },
    )
    root_rels = Element(f"{{{root_rel_ns}}}Relationships")
    SubElement(
        root_rels,
        f"{{{root_rel_ns}}}Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml",
        },
    )
    content_types = Element(f"{{{content_ns}}}Types")
    SubElement(
        content_types,
        f"{{{content_ns}}}Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    SubElement(
        content_types,
        f"{{{content_ns}}}Default",
        {
            "Extension": "xml",
            "ContentType": "application/xml",
        },
    )
    SubElement(
        content_types,
        f"{{{content_ns}}}Override",
        {
            "PartName": "/xl/workbook.xml",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
            ),
        },
    )
    SubElement(
        content_types,
        f"{{{content_ns}}}Override",
        {
            "PartName": "/xl/worksheets/sheet1.xml",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
            ),
        },
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml", tostring(content_types, encoding="utf-8", xml_declaration=True)
        )
        archive.writestr("_rels/.rels", tostring(root_rels, encoding="utf-8", xml_declaration=True))
        archive.writestr(
            "xl/workbook.xml", tostring(workbook, encoding="utf-8", xml_declaration=True)
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            tostring(workbook_rels, encoding="utf-8", xml_declaration=True),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml", tostring(worksheet, encoding="utf-8", xml_declaration=True)
        )
    return output.getvalue()


def boq_xlsx_export(boq):
    return rows_xlsx_export(boq_export_rows(boq))


@transaction.atomic
def create_boq(
    *,
    actor,
    project,
    title,
    reference="",
    currency="KES",
    tax_percent=Decimal("0"),
    contingency_percent=Decimal("0"),
):
    _require_project_role(
        actor, project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    tax_percent = _decimal(tax_percent, "Tax percentage")
    contingency_percent = _decimal(contingency_percent, "Contingency percentage")
    if tax_percent < 0 or contingency_percent < 0:
        raise ConstructionError("Tax and contingency percentages cannot be negative.")
    if tax_percent > Decimal("100") or contingency_percent > Decimal("100"):
        raise ConstructionError("Tax and contingency percentages cannot exceed 100.")
    if not reference:
        reference = f"BOQ-{project.code}-{project.boqs.count() + 1:04d}"
    if not currency.isalpha() or len(currency) != 3 or currency.upper() != currency:
        raise ConstructionError("Currency must be a three-letter uppercase ISO code.")
    boq = BoQ.objects.create(
        project=project,
        title=title,
        reference=reference.strip().upper(),
        currency=currency,
        tax_percent=tax_percent,
        contingency_percent=contingency_percent,
        created_by=actor,
    )
    audit(project, actor, "boq.created", "boq", boq.id)
    return boq


@transaction.atomic
def create_boq_section(*, actor, boq, code, title, sequence=1, parent=None):
    boq = BoQ.objects.select_for_update().select_related("project").get(pk=boq.pk)
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if boq.status != BoQStatus.DRAFT:
        raise ConstructionError("Only draft BoQs can be edited.")
    if parent and parent.boq_id != boq.id:
        raise ConstructionError("A subsection parent must belong to this BoQ.")
    section = BoQSection.objects.create(
        boq=boq,
        parent=parent,
        code=code.strip().upper(),
        title=title,
        sequence=sequence,
    )
    audit(boq.project, actor, "boq.section_created", "boq_section", section.id)
    return section


def _validate_item_values(*, quantity, unit_rate):
    quantity = _decimal(quantity, "Quantity")
    unit_rate = _decimal(unit_rate, "Unit rate")
    if quantity < 0:
        raise ConstructionError("Quantity cannot be negative.")
    if unit_rate < 0:
        raise ConstructionError("Unit rate cannot be negative.")
    return quantity, unit_rate


@transaction.atomic
def create_boq_item(
    *,
    actor,
    section,
    item_code,
    description,
    unit,
    quantity,
    unit_rate,
):
    section = (
        BoQSection.objects.select_for_update().select_related("boq__project").get(pk=section.pk)
    )
    boq = BoQ.objects.select_for_update().get(pk=section.boq_id)
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if boq.status != BoQStatus.DRAFT:
        raise ConstructionError("Only draft BoQs can be edited.")
    quantity, unit_rate = _validate_item_values(quantity=quantity, unit_rate=unit_rate)
    item = BoQItem.objects.create(
        section=section,
        item_code=item_code.strip().upper(),
        description=description,
        unit=unit.strip(),
        quantity=quantity,
        unit_rate=unit_rate,
        amount=_money(quantity * unit_rate),
    )
    calculate_boq(boq)
    audit(boq.project, actor, "boq.item_created", "boq_item", item.id)
    return item


@transaction.atomic
def update_boq_item(*, actor, item, **fields):
    item = (
        BoQItem.objects.select_for_update().select_related("section__boq__project").get(pk=item.pk)
    )
    boq = BoQ.objects.select_for_update().get(pk=item.section.boq_id)
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if boq.status != BoQStatus.DRAFT:
        raise ConstructionError("Only draft BoQs can be edited.")
    if {"quantity", "unit_rate"} & fields.keys():
        quantity, unit_rate = _validate_item_values(
            quantity=fields.get("quantity", item.quantity),
            unit_rate=fields.get("unit_rate", item.unit_rate),
        )
        item.quantity = quantity
        item.unit_rate = unit_rate
    for field in ("item_code", "description", "unit"):
        if field in fields:
            setattr(item, field, fields[field])
    item.amount = _money(item.quantity * item.unit_rate)
    item.save()
    calculate_boq(boq)
    audit(boq.project, actor, "boq.item_updated", "boq_item", item.id)
    return item


@transaction.atomic
def delete_boq_item(*, actor, item):
    item = (
        BoQItem.objects.select_for_update().select_related("section__boq__project").get(pk=item.pk)
    )
    boq = BoQ.objects.select_for_update().get(pk=item.section.boq_id)
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if boq.status != BoQStatus.DRAFT:
        raise ConstructionError("Only draft BoQs can be edited.")
    item_id = item.id
    item.delete()
    calculate_boq(boq)
    audit(boq.project, actor, "boq.item_deleted", "boq_item", item_id)


@transaction.atomic
def submit_boq(*, actor, boq):
    boq = BoQ.objects.select_for_update().select_related("project").get(pk=boq.pk)
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if boq.status != BoQStatus.DRAFT:
        raise ConstructionError("Only draft BoQs can be submitted.")
    calculate_boq(boq)
    boq.status = BoQStatus.SUBMITTED
    boq.submitted_by = actor
    boq.submitted_at = timezone.now()
    boq.save(update_fields=["status", "submitted_by", "submitted_at"])
    audit(boq.project, actor, "boq.submitted", "boq", boq.id, revision=boq.revision)
    return boq


@transaction.atomic
def review_boq(*, actor, boq):
    boq = BoQ.objects.select_for_update().select_related("project").get(pk=boq.pk)
    _require_project_role(actor, boq.project, PROJECT_MANAGEMENT_ROLES)
    if boq.status != BoQStatus.SUBMITTED:
        raise ConstructionError("Only submitted BoQs can be reviewed.")
    if boq.submitted_by_id == actor.id:
        raise ConstructionError("The BoQ submitter cannot review the same revision.")
    boq.status = BoQStatus.REVIEWED
    boq.reviewed_by = actor
    boq.reviewed_at = timezone.now()
    boq.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    audit(boq.project, actor, "boq.reviewed", "boq", boq.id, revision=boq.revision)
    return boq


@transaction.atomic
def approve_boq(*, actor, boq):
    boq = BoQ.objects.select_for_update().select_related("project").get(pk=boq.pk)
    project = boq.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if boq.status != BoQStatus.REVIEWED:
        raise ConstructionError("Only a reviewed BoQ can be approved.")
    if boq.reviewed_by_id == actor.id:
        raise ConstructionError("A BoQ reviewer cannot approve the same revision.")
    boq.status = BoQStatus.APPROVED
    boq.approved_at = timezone.now()
    boq.approved_by = actor
    boq.save(update_fields=["status", "approved_at", "approved_by"])
    audit(project, actor, "boq.approved", "boq", boq.id, revision=boq.revision)
    return boq


@transaction.atomic
def revise_boq(*, actor, boq):
    boq = BoQ.objects.select_for_update().get(pk=boq.pk)
    if boq.status != BoQStatus.APPROVED:
        raise ConstructionError("Only an approved BoQ can be revised.")
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    boq.status = BoQStatus.SUPERSEDED
    boq.save(update_fields=["status"])
    revision = BoQ.objects.create(
        project=boq.project,
        title=boq.title,
        revision=boq.revision + 1,
        revision_of=boq,
        currency=boq.currency,
        tax_percent=boq.tax_percent,
        contingency_percent=boq.contingency_percent,
        created_by=actor,
    )
    copied_sections = {}
    source_sections = list(boq.sections.all().order_by("sequence", "code"))
    for source_section in source_sections:
        section = BoQSection.objects.create(
            boq=revision,
            code=source_section.code,
            title=source_section.title,
            sequence=source_section.sequence,
        )
        copied_sections[source_section.id] = section
        for source_item in source_section.items.all():
            BoQItem.objects.create(
                section=section,
                item_code=source_item.item_code,
                description=source_item.description,
                unit=source_item.unit,
                quantity=source_item.quantity,
                unit_rate=source_item.unit_rate,
                amount=source_item.amount,
            )
    for source_section in source_sections:
        if source_section.parent_id:
            copied = copied_sections[source_section.id]
            copied.parent = copied_sections[source_section.parent_id]
            copied.save(update_fields=["parent"])
    audit(
        boq.project, actor, "boq.revision_created", "boq", revision.id, source_revision=boq.revision
    )
    return revision


@transaction.atomic
def create_project_budget(*, actor, project, name, currency="KES"):
    _require_project_role(
        actor, project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if not currency.isalpha() or len(currency) != 3 or currency.upper() != currency:
        raise ConstructionError("Currency must be a three-letter uppercase ISO code.")
    budget = ProjectBudget.objects.create(
        project=project, name=name, currency=currency, created_by=actor
    )
    audit(project, actor, "budget.created", "budget", budget.id, revision=budget.revision)
    return budget


@transaction.atomic
def create_budget_category(*, actor, budget, name, planned_amount):
    budget = ProjectBudget.objects.select_for_update().select_related("project").get(pk=budget.pk)
    _require_project_role(
        actor, budget.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if budget.status != BudgetStatus.DRAFT:
        raise ConstructionError("Only draft budgets can be edited.")
    planned_amount = _money(_decimal(planned_amount, "Planned amount"))
    if planned_amount < 0:
        raise ConstructionError("Planned amounts cannot be negative.")
    category = BudgetCategory.objects.create(
        budget=budget, name=name.strip(), planned_amount=planned_amount
    )
    _recalculate_budget_total(budget)
    audit(budget.project, actor, "budget.category_created", "budget_category", category.id)
    return category


def _recalculate_budget_total(budget):
    total = sum(
        budget.categories.values_list("planned_amount", flat=True),
        start=Decimal("0"),
    )
    budget.total = _money(total)
    budget.save(update_fields=["total"])
    return budget.total


@transaction.atomic
def submit_budget(*, actor, budget):
    budget = ProjectBudget.objects.select_for_update().select_related("project").get(pk=budget.pk)
    _require_project_role(
        actor, budget.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if budget.status != BudgetStatus.DRAFT:
        raise ConstructionError("Only draft budgets can be submitted.")
    if not budget.categories.exists():
        raise ConstructionError("A budget requires at least one category.")
    _recalculate_budget_total(budget)
    budget.status = BudgetStatus.SUBMITTED
    budget.save(update_fields=["status"])
    audit(budget.project, actor, "budget.submitted", "budget", budget.id)
    return budget


@transaction.atomic
def approve_budget(*, actor, budget):
    budget = ProjectBudget.objects.select_for_update().select_related("project").get(pk=budget.pk)
    _require_project_role(actor, budget.project, PROJECT_MANAGEMENT_ROLES)
    if budget.status != BudgetStatus.SUBMITTED:
        raise ConstructionError("Only submitted budgets can be approved.")
    if budget.revision_of_id:
        original = ProjectBudget.objects.select_for_update().get(pk=budget.revision_of_id)
        if original.status == BudgetStatus.APPROVED:
            original.status = BudgetStatus.SUPERSEDED
            original.save(update_fields=["status"])
    budget.status = BudgetStatus.APPROVED
    budget.approved_at = timezone.now()
    budget.approved_by = actor
    budget.save(update_fields=["status", "approved_at", "approved_by"])
    approved_total = ProjectBudget.objects.filter(
        project=budget.project, status=BudgetStatus.APPROVED
    ).aggregate(value=models.Sum("total"))["value"] or Decimal("0")
    ConstructionProject.objects.filter(pk=budget.project_id).update(
        approved_budget=_money(approved_total),
        updated_at=timezone.now(),
    )
    audit(budget.project, actor, "budget.approved", "budget", budget.id, total=str(budget.total))
    return budget


@transaction.atomic
def revise_budget(*, actor, budget):
    budget = ProjectBudget.objects.select_for_update().select_related("project").get(pk=budget.pk)
    _require_project_role(
        actor, budget.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if budget.status != BudgetStatus.APPROVED:
        raise ConstructionError("Only approved budgets can be revised.")
    revision = ProjectBudget.objects.create(
        project=budget.project,
        name=budget.name,
        revision=budget.revision + 1,
        revision_of=budget,
        currency=budget.currency,
        created_by=actor,
    )
    for category in budget.categories.all():
        BudgetCategory.objects.create(
            budget=revision,
            name=category.name,
            planned_amount=category.planned_amount,
        )
    _recalculate_budget_total(revision)
    audit(
        budget.project,
        actor,
        "budget.revision_created",
        "budget",
        revision.id,
        source_revision=budget.revision,
    )
    return revision


@transaction.atomic
def record_project_cost(*, actor, category, record_type, amount, reference, description=""):
    category = (
        BudgetCategory.objects.select_for_update()
        .select_related("budget__project")
        .get(pk=category.pk)
    )
    budget = ProjectBudget.objects.select_for_update().get(pk=category.budget_id)
    project = budget.project
    allowed = (
        PROJECT_MANAGEMENT_ROLES
        if record_type == "commitment"
        else {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    _require_project_role(actor, project, allowed)
    if budget.status != BudgetStatus.APPROVED:
        raise ConstructionError("Costs must reference a category in an approved budget.")
    if record_type not in {"commitment", "actual", "forecast"}:
        raise ConstructionError("Unknown cost record type.")
    amount = _money(_decimal(amount, "Cost amount"))
    if amount < 0:
        raise ConstructionError("Cost amounts cannot be negative.")
    if not reference.strip():
        raise ConstructionError("A cost reference is required.")
    existing = CostRecord.objects.filter(
        project=project, record_type=record_type, reference=reference.strip()
    ).first()
    if existing:
        if (
            existing.category_id != category.id
            or existing.amount != amount
            or existing.description != description
        ):
            raise ConstructionError(
                "That cost reference was already used with different data.", status_code=409
            )
        return existing
    record = CostRecord.objects.create(
        project=project,
        category=category,
        record_type=record_type,
        amount=amount,
        reference=reference.strip(),
        description=description,
        recorded_by=actor,
    )
    if record_type in {"commitment", "actual"}:
        field = "committed_amount" if record_type == "commitment" else "actual_amount"
        BudgetCategory.objects.filter(pk=category.pk).update(**{field: models.F(field) + amount})
    audit(
        project,
        actor,
        f"cost.{record_type}_recorded",
        "cost_record",
        record.id,
        amount=str(amount),
        reference=record.reference,
    )
    return record


@transaction.atomic
def request_variation(
    *,
    actor,
    project,
    reference,
    reason,
    scope_modification,
    cost_impact,
    time_impact_days=0,
    budget_category=None,
):
    _require_project_role(
        actor,
        project,
        {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR},
    )
    impact = _money(_decimal(cost_impact, "Variation cost impact"))
    if budget_category:
        budget_category = BudgetCategory.objects.select_related("budget").get(pk=budget_category.pk)
        if (
            budget_category.budget.project_id != project.id
            or budget_category.budget.status != BudgetStatus.APPROVED
        ):
            raise ConstructionError("Variation category must belong to an approved project budget.")
    variation = ApprovedVariation.objects.create(
        project=project,
        reference=reference.strip().upper(),
        reason=reason,
        scope_modification=scope_modification,
        cost_impact=impact,
        time_impact_days=time_impact_days,
        budget_category=budget_category,
        requested_by=actor,
    )
    audit(project, actor, "variation.requested", "variation", variation.id)
    return variation


@transaction.atomic
def review_variation(*, actor, variation):
    variation = (
        ApprovedVariation.objects.select_for_update().select_related("project").get(pk=variation.pk)
    )
    _require_project_role(actor, variation.project, PROJECT_MANAGEMENT_ROLES)
    if variation.status != VariationStatus.REQUESTED:
        raise ConstructionError("Only requested variations can be reviewed.")
    if variation.requested_by_id == actor.id:
        raise ConstructionError("A variation requester cannot review their own request.")
    variation.status = VariationStatus.REVIEW
    variation.submitted_at = timezone.now()
    variation.save(update_fields=["status", "submitted_at"])
    audit(variation.project, actor, "variation.review_started", "variation", variation.id)
    return variation


@transaction.atomic
def approve_variation(*, actor, variation):
    variation = (
        ApprovedVariation.objects.select_for_update().select_related("project").get(pk=variation.pk)
    )
    _require_project_role(actor, variation.project, PROJECT_MANAGEMENT_ROLES)
    if variation.status != VariationStatus.REVIEW:
        raise ConstructionError("Only reviewed variations can be approved.")
    if variation.requested_by_id == actor.id:
        raise ConstructionError("A variation requester cannot approve their own request.")
    variation.status = VariationStatus.APPROVED
    variation.approved_by = actor
    variation.approved_at = timezone.now()
    variation.save(update_fields=["status", "approved_by", "approved_at"])
    audit(
        variation.project,
        actor,
        "variation.approved",
        "variation",
        variation.id,
        cost_impact=str(variation.cost_impact),
    )
    return variation


@transaction.atomic
def reject_variation(*, actor, variation, reason):
    variation = (
        ApprovedVariation.objects.select_for_update().select_related("project").get(pk=variation.pk)
    )
    _require_project_role(actor, variation.project, PROJECT_MANAGEMENT_ROLES)
    if variation.status not in (VariationStatus.REQUESTED, VariationStatus.REVIEW):
        raise ConstructionError("This variation cannot be rejected in its current state.")
    variation.status = VariationStatus.REJECTED
    variation.rejected_by = actor
    variation.rejected_at = timezone.now()
    variation.rejection_reason = reason
    variation.save(update_fields=["status", "rejected_by", "rejected_at", "rejection_reason"])
    audit(variation.project, actor, "variation.rejected", "variation", variation.id)
    return variation


def project_cost_report(project):
    approved_budget = (
        ProjectBudget.objects.filter(project=project, status=BudgetStatus.APPROVED)
        .order_by("-revision")
        .first()
    )
    planned = approved_budget.total if approved_budget else Decimal("0")
    commitments = CostRecord.objects.filter(project=project, record_type="commitment").aggregate(
        value=models.Sum("amount")
    )["value"] or Decimal("0")
    actual = CostRecord.objects.filter(project=project, record_type="actual").aggregate(
        value=models.Sum("amount")
    )["value"] or Decimal("0")
    forecasts = CostRecord.objects.filter(project=project, record_type="forecast").aggregate(
        value=models.Sum("amount")
    )["value"] or Decimal("0")
    variation_total = ApprovedVariation.objects.filter(
        project=project, status=VariationStatus.APPROVED
    ).aggregate(value=models.Sum("cost_impact"))["value"] or Decimal("0")
    adjusted_budget = planned + variation_total
    categories = []
    if approved_budget:
        for category in approved_budget.categories.order_by("name"):
            categories.append(
                {
                    "category": category.name,
                    "planned": str(category.planned_amount),
                    "committed": str(category.committed_amount),
                    "actual": str(category.actual_amount),
                    "remaining_to_plan": str(
                        _money(category.planned_amount - category.actual_amount)
                    ),
                }
            )
    return {
        "project_id": str(project.id),
        "currency": approved_budget.currency if approved_budget else "KES",
        "approved_budget": str(_money(planned)),
        "approved_variations": str(_money(variation_total)),
        "current_budget": str(_money(adjusted_budget)),
        "committed_costs": str(_money(commitments)),
        "actual_costs": str(_money(actual)),
        "forecast_inputs": str(_money(forecasts)),
        "budget_variance_to_actual": str(_money(adjusted_budget - actual)),
        "cost_to_complete": str(_money(max(adjusted_budget - actual, Decimal("0")))),
        "cost_to_complete_basis": (
            "approved budget plus approved variations less recorded actual costs"
        ),
        "categories": categories,
    }


def project_cost_report_csv(project):
    import csv
    import io

    report = project_cost_report(project)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        _csv_safe_row(["Project cost report", report["project_id"], report["currency"]])
    )
    for key in (
        "approved_budget",
        "approved_variations",
        "current_budget",
        "committed_costs",
        "actual_costs",
        "forecast_inputs",
        "budget_variance_to_actual",
        "cost_to_complete",
    ):
        writer.writerow(_csv_safe_row([key, report[key]]))
    writer.writerow([])
    writer.writerow(
        _csv_safe_row(["category", "planned", "committed", "actual", "remaining_to_plan"])
    )
    writer.writerows(
        _csv_safe_row(
            [
                row["category"],
                row["planned"],
                row["committed"],
                row["actual"],
                row["remaining_to_plan"],
            ]
        )
        for row in report["categories"]
    )
    return output.getvalue()


def _csv_safe_row(values):
    return [
        f"'{value}" if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
        for value in values
    ]


@transaction.atomic
def submit_and_approve_variation(*, actor, variation):
    return approve_variation(actor=actor, variation=variation)


def validate_milestone_dependencies(project, milestone, dependent_ids):
    candidate_id = str(milestone.id)
    if candidate_id in dependent_ids:
        raise ConstructionError("A milestone cannot depend on itself.")
    ids = set(dependent_ids)
    milestones = {
        str(value.id): value for value in ProjectMilestone.objects.filter(project=project)
    }
    if not ids.issubset(milestones):
        raise ConstructionError("Dependencies must belong to the same project.")
    for dependency_id in ids:
        seen = set()
        stack = [dependency_id]
        while stack:
            node_id = stack.pop()
            if node_id == candidate_id:
                raise ConstructionError("Milestone dependencies contain a cycle.")
            if node_id in seen:
                continue
            seen.add(node_id)
            node = milestones.get(node_id)
            if node:
                stack.extend(
                    str(dep_id) for dep_id in node.dependencies.values_list("id", flat=True)
                )
    return


@transaction.atomic
def set_milestone_dependencies(*, actor, milestone, dependent_ids):
    _require_project_role(actor, milestone.project, PROJECT_MANAGEMENT_ROLES)
    validate_milestone_dependencies(milestone.project, milestone, dependent_ids)
    dependencies = list(
        ProjectMilestone.objects.filter(project=milestone.project, id__in=dependent_ids)
    )
    milestone.dependencies.set(dependencies)
    audit(milestone.project, actor, "milestone.dependencies_changed", "milestone", milestone.id)
    return milestone


@transaction.atomic
def set_activity_dependencies(*, actor, activity, dependent_ids):
    _require_project_role(actor, activity.project, PROJECT_MANAGEMENT_ROLES)
    candidate_id = str(activity.id)
    ids = {str(value) for value in dependent_ids}
    if candidate_id in ids:
        raise ConstructionError("An activity cannot depend on itself.")
    nodes = {
        str(item.id): item for item in ProjectActivity.objects.filter(project=activity.project)
    }
    if not ids.issubset(nodes):
        raise ConstructionError("Activity dependencies must belong to the same project.")
    for start_id in ids:
        seen = set()
        stack = [start_id]
        while stack:
            node_id = stack.pop()
            if node_id == candidate_id:
                raise ConstructionError("Activity dependencies contain a cycle.")
            if node_id in seen:
                continue
            seen.add(node_id)
            node = nodes.get(node_id)
            if node:
                stack.extend(
                    str(dep_id) for dep_id in node.dependencies.values_list("id", flat=True)
                )
    activity.dependencies.set(ProjectActivity.objects.filter(project=activity.project, id__in=ids))
    audit(activity.project, actor, "activity.dependencies_changed", "activity", activity.id)
    return activity


@transaction.atomic
def update_milestone_progress(*, actor, milestone, progress_percent, blockers=""):
    _require_project_role(
        actor, milestone.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.CONTRACTOR}
    )
    if progress_percent < 0 or progress_percent > 100:
        raise ConstructionError("Progress percent must be between 0 and 100.")
    milestone.progress_percent = Decimal(str(progress_percent)).quantize(MONEY_QUANTUM)
    milestone.blockers = blockers
    if progress_percent == 100:
        milestone.completed_confirmed_by = actor
        if milestone.actual_end is None:
            milestone.actual_end = timezone.now().date()
    milestone.save(
        update_fields=["progress_percent", "blockers", "completed_confirmed_by", "actual_end"]
    )
    audit(
        milestone.project,
        actor,
        "milestone.progress_updated",
        "milestone",
        milestone.id,
        progress=str(milestone.progress_percent),
    )
    return milestone


@transaction.atomic
def submit_rfi(*, actor, rfi):
    project = rfi.project
    _require_project_role(
        actor,
        project,
        {
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.CIVIL_ENGINEER,
            ProjectRole.ARCHITECT,
            ProjectRole.QUANTITY_SURVEYOR,
        },
    )
    if rfi.status != RFIStatus.DRAFT:
        raise ConstructionError("Only draft RFIs can be submitted.")
    rfi.status = RFIStatus.SUBMITTED
    rfi.submitted_at = timezone.now()
    rfi.save(update_fields=["status", "submitted_at"])
    audit(project, actor, "rfi.submitted", "rfi", rfi.id)
    return rfi


@transaction.atomic
def respond_rfi(*, actor, rfi, response):
    project = rfi.project
    if rfi.responsible_recipient_id != actor.id:
        _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if rfi.status == RFIStatus.CLOSED:
        raise ConstructionError("Closed RFIs cannot be changed.")
    rfi.status = RFIStatus.RESPONDING
    rfi.response = response
    rfi.save(update_fields=["status", "response"])
    audit(project, actor, "rfi.responded", "rfi", rfi.id)
    return rfi


@transaction.atomic
def close_rfi(*, actor, rfi):
    project = rfi.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    rfi.status = RFIStatus.CLOSED
    rfi.closed_at = timezone.now()
    rfi.save(update_fields=["status", "closed_at"])
    audit(project, actor, "rfi.closed", "rfi", rfi.id)
    return rfi


@transaction.atomic
def award_tender(*, actor, tender, contractor):
    project = tender.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if tender.status not in (TenderStatus.INVITED, TenderStatus.CLOSED):
        raise ConstructionError("Tender cannot be awarded in its current state.")
    if not TenderBid.objects.filter(tender=tender, bidder=contractor).exists():
        raise ConstructionError("Only a bidder can receive the award.")
    tender.status = TenderStatus.AWARDED
    tender.awarded_contractor = contractor
    tender.save(update_fields=["status", "awarded_contractor"])
    audit(project, actor, "tender.awarded", "tender", tender.id, contractor=str(contractor.id))
    return tender


def project_report(project):
    return {
        "project": {
            "id": str(project.id),
            "code": project.code,
            "name": project.name,
            "status": project.status,
            "budget": str(project.budget),
        },
        "milestones": [
            {
                "id": str(item.id),
                "name": item.name,
                "progress_percent": str(item.progress_percent),
                "blockers": item.blockers,
            }
            for item in project.milestones.all()
        ],
        "inspections": [
            {
                "id": str(item.id),
                "title": item.title,
                "status": item.status,
                "inspection_date": item.inspection_date.isoformat(),
                "open_defects": item.defects.exclude(status=DefectStatus.CLOSED).count(),
            }
            for item in project.inspections.all()
        ],
        "boq_total": str(
            max(
                (boq.grand_total for boq in project.boqs.filter(status=BoQStatus.APPROVED)),
                default=Decimal("0"),
            )
        ),
        "variations": [
            {
                "id": str(item.id),
                "reference": item.reference,
                "status": item.status,
                "cost_impact": str(item.cost_impact),
            }
            for item in project.variations.all()
        ],
        "source_revision": project.version,
    }


def project_report_csv(project):
    import csv
    import io

    report = project_report(project)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_csv_safe_row(["Glitters Construction Project Report"]))
    writer.writerow(_csv_safe_row(["project_code", report["project"]["code"]]))
    writer.writerow(_csv_safe_row(["project_name", report["project"]["name"]]))
    writer.writerow(_csv_safe_row(["status", report["project"]["status"]]))
    writer.writerow(_csv_safe_row(["budget", report["project"]["budget"]]))
    writer.writerow(_csv_safe_row(["boq_total", report["boq_total"]]))
    writer.writerow([])
    writer.writerow(["milestone", "progress_percent", "blockers"])
    for milestone in report["milestones"]:
        writer.writerow(
            _csv_safe_row(
                [
                    milestone["name"],
                    milestone["progress_percent"],
                    milestone["blockers"],
                ]
            )
        )
    writer.writerow([])
    writer.writerow(["inspection", "status", "date", "open_defects"])
    for inspection in report["inspections"]:
        writer.writerow(
            _csv_safe_row(
                [
                    inspection["title"],
                    inspection["status"],
                    inspection["inspection_date"],
                    inspection["open_defects"],
                ]
            )
        )
    return output.getvalue()
