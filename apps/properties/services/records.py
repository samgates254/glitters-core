"""Create, update, and archive property records."""

from __future__ import annotations

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.locations.services.resolution import persist_address, resolve_address
from apps.organizations.domain import Permission
from apps.organizations.services.authorization import (
    authorize,
    ensure_active_account,
    load_organization,
    lock_active_membership,
)
from apps.properties.domain import (
    ADDRESS_REQUIRED,
    ALREADY_ARCHIVED,
    ARCHIVE_CHILDREN,
    ARCHIVE_VIA_OPERATION,
    ARCHIVED,
    CHARACTERISTIC_TYPE,
    CONFLICT,
    CONTEXT_BOTH,
    CONTEXT_IMMUTABLE,
    CONTEXT_MISMATCH,
    HIERARCHY_INVALID,
    ORGANIZATION_REQUIRED,
    PERMISSION_DENIED,
    STATUS_INVALID,
    SUBTYPE_INVALID,
    AccessCapability,
    AccessOrigin,
    AccessStatus,
    EventAction,
    HierarchyRole,
    ManagementContext,
    PropertyStatus,
    hierarchy_parent_allowed,
    new_reference,
    parse_coordinates,
    subtype_allowed,
    transition_allowed,
)
from apps.properties.errors import PropertyError
from apps.properties.models import Property, PropertyAccess, PropertyLocation
from apps.properties.services.audit import record_event
from apps.properties.services.authorization import decide, require_archive, require_update
from apps.properties.services.characteristics import replace_characteristics
from apps.properties.services.locking import is_deadlock, lock_properties

CONTEXT_INVALID = "This management context is not valid."
NAME_INVALID = "This name is not valid."
DESCRIPTION_INVALID = "This description is not valid."


def create_property(*, actor, data: dict, request=None) -> Property:
    ensure_active_account(actor)
    context, organization_id = _context(data)
    if context == ManagementContext.ORGANIZATION:
        authorize(actor, organization_id, Permission.PROPERTY_CREATE)
    try:
        return _create_locked(actor, data, context, organization_id, request)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def update_property(*, actor, property_id, data: dict, request=None) -> Property:
    decision = decide(actor, property_id)
    require_update(decision)
    try:
        with transaction.atomic():
            return _update_locked(actor, property_id, data, request)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def archive_property(*, actor, property_id, request=None) -> Property:
    decision = decide(actor, property_id)
    require_archive(decision)
    try:
        with transaction.atomic():
            record = lock_properties(property_id)[property_id]
            if record.status == PropertyStatus.ARCHIVED:
                raise PropertyError(ALREADY_ARCHIVED, status_code=409)
            children = Property.objects.select_for_update().filter(parent=record).order_by("id")
            if children.filter(status__in=[PropertyStatus.DRAFT, PropertyStatus.ACTIVE]).exists():
                raise PropertyError(ARCHIVE_CHILDREN, status_code=409)
            record.status = PropertyStatus.ARCHIVED
            record.archived_at = timezone.now()
            record.save(update_fields=["status", "archived_at", "updated_at"])
            record_event(
                actor=actor,
                property_record=record,
                action=EventAction.ARCHIVED,
                object_type="property",
                object_id=record.id,
                request=request,
                metadata={"status": record.status},
            )
            return record
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def _create_locked(actor, data, context, organization_id, request) -> Property:
    with transaction.atomic():
        organization = None
        if context == ManagementContext.ORGANIZATION:
            organization = load_organization(organization_id, lock=True)
            lock_active_membership(organization, actor, Permission.PROPERTY_CREATE)
        parent = _locked_parent(actor, data.get("parent_id"), context, organization_id)
        role = data.get("hierarchy_role")
        if role not in HierarchyRole.VALUES:
            raise PropertyError(HIERARCHY_INVALID, status_code=400)
        parent_role = None if parent is None else parent.hierarchy_role
        if not hierarchy_parent_allowed(role, parent_role):
            raise PropertyError(HIERARCHY_INVALID, status_code=400)
        status = data.get("status") or PropertyStatus.DRAFT
        if status == PropertyStatus.ARCHIVED:
            raise PropertyError(ARCHIVE_VIA_OPERATION, status_code=400)
        if status not in {PropertyStatus.DRAFT, PropertyStatus.ACTIVE}:
            raise PropertyError(STATUS_INVALID, status_code=400)
        property_type, subtype = _types(data)
        name = _name(data.get("name"))
        description = _description(data.get("description", ""))
        record = _insert_record(
            actor=actor,
            name=name,
            description=description,
            property_type=property_type,
            property_subtype=subtype,
            status=status,
            hierarchy_role=role,
            parent=parent,
            context=context,
            organization=organization,
        )
        _apply_location(
            record,
            address_payload=data.get("address") if "address" in data else None,
            address_present="address" in data,
            coordinates_payload=data.get("coordinates") if "coordinates" in data else None,
            coordinates_present="coordinates" in data,
        )
        _require_address_if_active(record)
        if context == ManagementContext.PERSONAL:
            _seed_personal_access(record, actor, parent, request)
        if data.get("characteristics"):
            replace_characteristics(
                record,
                data["characteristics"],
                actor=actor,
                request=request,
            )
        record_event(
            actor=actor,
            property_record=record,
            action=EventAction.CREATED,
            object_type="property",
            object_id=record.id,
            request=request,
            metadata={
                "management_context": context,
                "property_type": property_type,
                "property_subtype": subtype,
                "status": status,
                "hierarchy_role": role,
                "coordinates_updated": bool(data.get("coordinates")),
            },
        )
        return _reread(record.id)


def _update_locked(actor, property_id, data, request) -> Property:
    record = lock_properties(property_id)[property_id]
    if record.status == PropertyStatus.ARCHIVED:
        raise PropertyError(ARCHIVED, status_code=409)
    if data.get("management_context", record.management_context) != record.management_context:
        raise PropertyError(CONTEXT_IMMUTABLE, status_code=400)
    if "organization_id" in data and data["organization_id"] != record.managing_organization_id:
        raise PropertyError(CONTEXT_IMMUTABLE, status_code=400)
    changed: list[str] = []
    property_type = data.get("property_type", record.property_type)
    subtype = data.get("property_subtype", record.property_subtype)
    if not subtype_allowed(property_type, subtype):
        raise PropertyError(SUBTYPE_INVALID, status_code=400)
    if "characteristics" not in data:
        _existing_characteristics_fit(record, property_type)
    if property_type != record.property_type:
        record.property_type = property_type
        changed.append("property_type")
    if subtype != record.property_subtype:
        record.property_subtype = subtype
        changed.append("property_subtype")
    if "name" in data:
        record.name = _name(data["name"])
        changed.append("name")
    if "description" in data:
        record.description = _description(data["description"])
        changed.append("description")
    if "status" in data and data["status"] != record.status:
        if data["status"] == PropertyStatus.ARCHIVED:
            raise PropertyError(ARCHIVE_VIA_OPERATION, status_code=400)
        if not transition_allowed(record.status, data["status"]):
            raise PropertyError(STATUS_INVALID, status_code=400)
        record.status = data["status"]
        changed.append("status")
    _apply_hierarchy(actor, record, data, changed)
    coordinates_updated = _apply_location(
        record,
        address_payload=data.get("address") if "address" in data else None,
        address_present="address" in data,
        coordinates_payload=data.get("coordinates") if "coordinates" in data else None,
        coordinates_present="coordinates" in data,
    )
    if coordinates_updated:
        changed.append("coordinates_updated")
    if "address" in data:
        changed.append("address_updated")
    _require_address_if_active(record)
    if "characteristics" in data:
        replace_characteristics(record, data["characteristics"], actor=actor, request=request)
        changed.append("characteristics")
    if not changed:
        raise PropertyError("No changes were submitted.", status_code=400)
    record.save()
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.UPDATED,
        object_type="property",
        object_id=record.id,
        request=request,
        metadata={
            "fields": [field for field in changed if field != "coordinates_updated"],
            "coordinates_updated": "coordinates_updated" in changed,
        },
    )
    return _reread(record.id)


def _context(data: dict) -> tuple[str, object]:
    context = data.get("management_context")
    if context not in ManagementContext.VALUES:
        raise PropertyError(CONTEXT_INVALID, status_code=400)
    organization_id = data.get("organization_id")
    if context == ManagementContext.PERSONAL:
        if organization_id:
            raise PropertyError(CONTEXT_BOTH, status_code=400)
        return context, None
    if not organization_id:
        raise PropertyError(ORGANIZATION_REQUIRED, status_code=400)
    return context, organization_id


def _types(data: dict) -> tuple[str, str]:
    property_type = data.get("property_type")
    subtype = data.get("property_subtype")
    if not subtype_allowed(property_type, subtype):
        raise PropertyError(SUBTYPE_INVALID, status_code=400)
    return property_type, subtype


def _name(value) -> str:
    if not isinstance(value, str):
        raise PropertyError(NAME_INVALID, status_code=400)
    name = value.strip()
    if not name or len(name) > 200:
        raise PropertyError(NAME_INVALID, status_code=400)
    return name


def _description(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 4000:
        raise PropertyError(DESCRIPTION_INVALID, status_code=400)
    return value.strip()


def _insert_record(**kwargs) -> Property:
    for _attempt in range(5):
        try:
            with transaction.atomic():
                return Property.objects.create(
                    reference=new_reference(),
                    name=kwargs["name"],
                    description=kwargs["description"],
                    property_type=kwargs["property_type"],
                    property_subtype=kwargs["property_subtype"],
                    status=kwargs["status"],
                    hierarchy_role=kwargs["hierarchy_role"],
                    parent=kwargs["parent"],
                    management_context=kwargs["context"],
                    managing_organization=kwargs["organization"],
                    created_by=kwargs["actor"],
                )
        except IntegrityError as exc:
            if "reference" not in str(exc):
                raise
    raise PropertyError(CONFLICT, status_code=409)


def _locked_parent(actor, parent_id, context, organization_id):
    if not parent_id:
        return None
    decision = decide(actor, parent_id)
    if not decision.can_update:
        raise PropertyError(PERMISSION_DENIED, status_code=403)
    parent = lock_properties(parent_id)[parent_id]
    if parent.status == PropertyStatus.ARCHIVED:
        raise PropertyError(ARCHIVED, status_code=409)
    if parent.management_context != context or parent.managing_organization_id != organization_id:
        raise PropertyError(CONTEXT_MISMATCH, status_code=400)
    return parent


def _apply_hierarchy(actor, record, data, changed: list[str]) -> None:
    role = data.get("hierarchy_role", record.hierarchy_role)
    if role not in HierarchyRole.VALUES:
        raise PropertyError(HIERARCHY_INVALID, status_code=400)
    parent_id = record.parent_id
    if "parent_id" in data:
        parent_id = data["parent_id"]
    if parent_id == record.id:
        raise PropertyError(HIERARCHY_INVALID, status_code=400)
    if parent_id is not None:
        decision = decide(actor, parent_id)
        if not decision.can_update:
            raise PropertyError(PERMISSION_DENIED, status_code=403)
        lock_properties(record.id, parent_id)
        parent = Property.objects.get(pk=parent_id)
        if parent.status == PropertyStatus.ARCHIVED:
            raise PropertyError(ARCHIVED, status_code=409)
        if (
            parent.management_context != record.management_context
            or parent.managing_organization_id != record.managing_organization_id
        ):
            raise PropertyError(CONTEXT_MISMATCH, status_code=400)
        parent_role = parent.hierarchy_role
    else:
        parent_role = None
    if not hierarchy_parent_allowed(role, parent_role):
        raise PropertyError(HIERARCHY_INVALID, status_code=400)
    if role != record.hierarchy_role:
        children = Property.objects.select_for_update().filter(parent=record).order_by("id")
        for child in children:
            if not hierarchy_parent_allowed(child.hierarchy_role, role):
                raise PropertyError(HIERARCHY_INVALID, status_code=400)
        record.hierarchy_role = role
        changed.append("hierarchy_role")
    if parent_id != record.parent_id:
        record.parent_id = parent_id
        changed.append("parent_id")


def _apply_location(
    record,
    *,
    address_payload,
    address_present: bool,
    coordinates_payload,
    coordinates_present: bool,
) -> bool:
    if not address_present and not coordinates_present:
        return False
    location = PropertyLocation.objects.filter(property=record).first()
    coordinates_updated = False
    if address_present:
        if address_payload is None:
            if location is not None and location.address_id is not None:
                old_address = location.address
                location.address = None
                location.save(update_fields=["address"])
                old_address.delete()
        else:
            resolved = resolve_address(address_payload)
            address = persist_address(resolved)
            if location is None:
                location = PropertyLocation.objects.create(property=record, address=address)
            else:
                old_address = location.address
                location.address = address
                location.save(update_fields=["address"])
                if old_address is not None:
                    old_address.delete()
    if coordinates_present:
        point = None
        if coordinates_payload is not None:
            try:
                point = parse_coordinates(coordinates_payload)
            except ValueError as exc:
                raise PropertyError(str(exc), status_code=400) from exc
        if location is None:
            location = PropertyLocation.objects.create(property=record)
        location.coordinates = point
        location.coordinates_set_at = timezone.now() if point is not None else None
        location.save(update_fields=["coordinates", "coordinates_set_at"])
        coordinates_updated = True
    return coordinates_updated


def _require_address_if_active(record) -> None:
    if record.status != PropertyStatus.ACTIVE:
        return
    location = PropertyLocation.objects.filter(property=record).only("address_id").first()
    if location is None or location.address_id is None:
        raise PropertyError(ADDRESS_REQUIRED, status_code=400)


def _existing_characteristics_fit(record, property_type: str) -> None:
    from apps.properties.domain import ATTRIBUTE_CATALOGUE

    for attribute in record.attributes.all():
        spec = ATTRIBUTE_CATALOGUE.get(attribute.code)
        if spec is None or property_type not in spec.property_types:
            raise PropertyError(CHARACTERISTIC_TYPE, status_code=409)


def _seed_personal_access(record, actor, parent, request) -> None:
    copied_manage = False
    if parent is not None:
        grants = PropertyAccess.objects.filter(property=parent, status=AccessStatus.ACTIVE)
        for grant in grants:
            created = PropertyAccess.objects.create(
                property=record,
                user=grant.user,
                capability=grant.capability,
                status=AccessStatus.ACTIVE,
                origin=AccessOrigin.INHERITED,
                inherited_from=grant,
                granted_by=actor,
            )
            record_event(
                actor=actor,
                property_record=record,
                action=EventAction.ACCESS_GRANTED,
                object_type="property_access",
                object_id=created.id,
                request=request,
                metadata={
                    "user_id": str(grant.user_id),
                    "capability": grant.capability,
                    "source": "parent",
                    "origin": AccessOrigin.INHERITED,
                },
            )
            if grant.user_id == actor.id and grant.capability == AccessCapability.MANAGE:
                copied_manage = True
    if parent is None or not copied_manage:
        created = PropertyAccess.objects.create(
            property=record,
            user=actor,
            capability=AccessCapability.MANAGE,
            status=AccessStatus.ACTIVE,
            origin=AccessOrigin.DIRECT,
            inherited_from=None,
            granted_by=actor,
        )
        record_event(
            actor=actor,
            property_record=record,
            action=EventAction.ACCESS_GRANTED,
            object_type="property_access",
            object_id=created.id,
            request=request,
            metadata={
                "user_id": str(actor.id),
                "capability": AccessCapability.MANAGE,
                "source": "creator",
                "origin": AccessOrigin.DIRECT,
            },
        )


def _reread(property_id) -> Property:
    return Property.objects.select_related(
        "location__address__country",
        "location__address__administrative_area",
        "location__address__locality",
        "managing_organization",
        "parent",
        "created_by",
    ).get(pk=property_id)
