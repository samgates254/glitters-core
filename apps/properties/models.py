"""Canonical property records, characteristics, parties, and access grants.

The managing organization is not the legal owner. A party role is a claim.
Selecting owner does not verify title. Coordinates live on PropertyLocation
and are omitted from the default API representation.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.gis.db.models import PointField
from django.db import models

from apps.locations.models import Address
from apps.organizations.models import Organization
from apps.properties.domain import (
    ATTRIBUTE_CATALOGUE,
    ATTRIBUTE_UNITS,
    PROPERTY_SUBTYPES,
    AccessCapability,
    AccessOrigin,
    AccessStatus,
    EventAction,
    HierarchyRole,
    ManagementContext,
    PartyRole,
    PartyStatus,
    PartySubject,
    PropertyStatus,
    PropertyType,
)


def _subtype_condition():
    pairs = list(PROPERTY_SUBTYPES.items())
    first_type, first_subtypes = pairs[0]
    allowed = models.Q(property_type=first_type, property_subtype__in=sorted(first_subtypes))
    for property_type, subtypes in pairs[1:]:
        allowed |= models.Q(property_type=property_type, property_subtype__in=sorted(subtypes))
    return allowed


class Property(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    property_type = models.CharField(max_length=32, choices=PropertyType.CHOICES)
    property_subtype = models.CharField(max_length=32)
    status = models.CharField(
        max_length=16,
        choices=PropertyStatus.CHOICES,
        default=PropertyStatus.DRAFT,
    )
    hierarchy_role = models.CharField(max_length=16, choices=HierarchyRole.CHOICES)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    management_context = models.CharField(max_length=16, choices=ManagementContext.CHOICES)
    managing_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="managed_properties",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_properties",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "property"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(PropertyStatus.VALUES)),
                name="property_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(property_type__in=sorted(PropertyType.VALUES)),
                name="property_type_valid",
            ),
            models.CheckConstraint(condition=_subtype_condition(), name="property_subtype_pair"),
            models.CheckConstraint(
                condition=models.Q(hierarchy_role__in=sorted(HierarchyRole.VALUES)),
                name="property_hierarchy_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(management_context__in=sorted(ManagementContext.VALUES)),
                name="property_context_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        management_context=ManagementContext.PERSONAL,
                        managing_organization__isnull=True,
                    )
                    | models.Q(
                        management_context=ManagementContext.ORGANIZATION,
                        managing_organization__isnull=False,
                    )
                ),
                name="property_management_context",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=PropertyStatus.ARCHIVED, archived_at__isnull=False)
                    | (
                        models.Q(status__in=[PropertyStatus.DRAFT, PropertyStatus.ACTIVE])
                        & models.Q(archived_at__isnull=True)
                    )
                ),
                name="property_archived_timestamp",
            ),
            models.CheckConstraint(condition=~models.Q(name=""), name="property_name_present"),
            models.CheckConstraint(
                condition=~models.Q(parent_id=models.F("id")),
                name="property_parent_not_self",
            ),
        ]
        indexes = [
            models.Index(
                fields=["managing_organization", "status"],
                name="property_org_status_idx",
            ),
            models.Index(fields=["property_type", "status"], name="property_type_status_idx"),
        ]

    def __str__(self) -> str:
        return self.reference


class PropertyLocation(models.Model):
    """Address and optional exact point. The point is not a public field."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.OneToOneField(Property, on_delete=models.CASCADE, related_name="location")
    address = models.ForeignKey(
        Address,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="property_locations",
    )
    coordinates = PointField(geography=True, srid=4326, null=True, blank=True, spatial_index=True)
    coordinates_set_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "property_location"

    def __str__(self) -> str:
        return f"location {self.property_id}"


class PropertyAttribute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="attributes")
    code = models.CharField(max_length=40)
    value_integer = models.IntegerField(null=True, blank=True)
    value_decimal = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    value_text = models.CharField(max_length=80, blank=True, default="")
    unit = models.CharField(max_length=16)

    class Meta:
        db_table = "property_attribute"
        constraints = [
            models.UniqueConstraint(fields=["property", "code"], name="property_attribute_code"),
            models.CheckConstraint(
                condition=models.Q(code__in=sorted(ATTRIBUTE_CATALOGUE)),
                name="property_attribute_code_known",
            ),
            models.CheckConstraint(
                condition=models.Q(unit__in=sorted(ATTRIBUTE_UNITS)),
                name="property_attribute_unit_known",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(value_integer__isnull=False, value_decimal__isnull=True, value_text="")
                    | models.Q(
                        value_integer__isnull=True,
                        value_decimal__isnull=False,
                        value_text="",
                    )
                    | models.Q(value_integer__isnull=True, value_decimal__isnull=True)
                    & ~models.Q(value_text="")
                ),
                name="property_attribute_one_value",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(value_integer__isnull=True)
                    | models.Q(value_integer__gte=-20, value_integer__lte=100000)
                ),
                name="property_attribute_integer_range",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(value_decimal__isnull=True)
                    | models.Q(value_decimal__gte=0, value_decimal__lte=1_000_000_000)
                ),
                name="property_attribute_decimal_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.property_id}"


class PropertyParty(models.Model):
    """A claimed relationship. Status verified is not available to this API."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="parties")
    subject_kind = models.CharField(max_length=16, choices=PartySubject.CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="property_parties",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="property_parties",
    )
    role = models.CharField(max_length=32, choices=PartyRole.CHOICES)
    status = models.CharField(max_length=32, choices=PartyStatus.CHOICES)
    note = models.CharField(max_length=500, blank=True, default="")
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="property_claims_made",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_source = models.CharField(max_length=80, blank=True, default="")
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "property_party"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        subject_kind=PartySubject.PERSON,
                        user__isnull=False,
                        organization__isnull=True,
                    )
                    | models.Q(
                        subject_kind=PartySubject.ORGANIZATION,
                        user__isnull=True,
                        organization__isnull=False,
                    )
                ),
                name="property_party_subject",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=sorted(PartyRole.VALUES)),
                name="property_party_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(PartyStatus.VALUES)),
                name="property_party_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=PartyStatus.VERIFIED)
                    | (models.Q(verified_at__isnull=False) & ~models.Q(verification_source=""))
                ),
                name="property_party_verified_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=PartyStatus.REVOKED, revoked_at__isnull=False)
                    | (~models.Q(status=PartyStatus.REVOKED) & models.Q(revoked_at__isnull=True))
                ),
                name="property_party_revoked_timestamp",
            ),
            models.UniqueConstraint(
                fields=["property", "user", "role"],
                condition=models.Q(
                    status__in=sorted(PartyStatus.OPEN),
                    subject_kind=PartySubject.PERSON,
                ),
                name="property_party_open_person",
            ),
            models.UniqueConstraint(
                fields=["property", "organization", "role"],
                condition=models.Q(
                    status__in=sorted(PartyStatus.OPEN),
                    subject_kind=PartySubject.ORGANIZATION,
                ),
                name="property_party_open_org",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role} {self.property_id}"


class PropertyAccess(models.Model):
    """Explicit permission to see or operate one property. Not a title."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="access_grants")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="property_access_grants",
    )
    capability = models.CharField(max_length=16, choices=AccessCapability.CHOICES)
    status = models.CharField(max_length=16, choices=AccessStatus.CHOICES)
    origin = models.CharField(
        max_length=16,
        choices=AccessOrigin.CHOICES,
        default=AccessOrigin.DIRECT,
    )
    inherited_from = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derived_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="property_access_granted",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "property_access"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capability__in=sorted(AccessCapability.VALUES)),
                name="property_access_capability_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AccessStatus.VALUES)),
                name="property_access_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(origin__in=sorted(AccessOrigin.VALUES)),
                name="property_access_origin_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin=AccessOrigin.DIRECT, inherited_from__isnull=True)
                    | models.Q(origin=AccessOrigin.INHERITED, inherited_from__isnull=False)
                ),
                name="property_access_origin_provenance",
            ),
            models.CheckConstraint(
                condition=~models.Q(inherited_from_id=models.F("id")),
                name="property_access_not_inherited_from_self",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=AccessStatus.REVOKED, revoked_at__isnull=False)
                    | (models.Q(status=AccessStatus.ACTIVE, revoked_at__isnull=True))
                ),
                name="property_access_revoked_timestamp",
            ),
            models.UniqueConstraint(
                fields=["property", "user"],
                condition=models.Q(status=AccessStatus.ACTIVE),
                name="property_access_one_active_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.capability} {self.user_id}"


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Property events are append-only.")

    def delete(self):
        raise ValueError("Property events are append-only.")


class PropertyEvent(models.Model):
    """Append-only property audit. Metadata must not contain coordinates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="property_events",
    )
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="events")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="property_events",
    )
    action = models.CharField(max_length=64, choices=EventAction.CHOICES)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=36)
    request_id = models.CharField(max_length=128, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "property_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(EventAction.VALUES)),
                name="property_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "created_at"], name="property_event_created"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Property events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Property events are append-only.")

    def __str__(self) -> str:
        return f"{self.action} {self.property_id}"
