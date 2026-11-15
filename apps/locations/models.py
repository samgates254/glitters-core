"""Reference geography and structured addresses.

Administrative names differ by country. A level row defines the ladder.
An area row is one node on that ladder. Coordinates are not stored here.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.locations.domain import COUNTRY_RE, LEVEL_CODE_RE, LocalityKind


class Country(models.Model):
    iso_alpha2 = models.CharField(max_length=2, primary_key=True)
    iso_alpha3 = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "country"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(iso_alpha2__regex=COUNTRY_RE.pattern),
                name="country_iso2",
            ),
            models.CheckConstraint(
                condition=models.Q(iso_alpha3__regex=r"^[A-Z]{3}$"),
                name="country_iso3",
            ),
            models.CheckConstraint(condition=~models.Q(name=""), name="country_name_present"),
        ]

    def __str__(self) -> str:
        return self.name


class AdministrativeLevel(models.Model):
    """One step on a country's administrative ladder. Depth 1 is the top."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="levels")
    depth = models.PositiveSmallIntegerField()
    code = models.CharField(max_length=32)
    label = models.CharField(max_length=80)

    class Meta:
        db_table = "administrative_level"
        ordering = ["country", "depth"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "depth"],
                name="administrative_level_depth",
            ),
            models.UniqueConstraint(
                fields=["country", "code"],
                name="administrative_level_code",
            ),
            models.CheckConstraint(
                condition=models.Q(depth__gte=1, depth__lte=8),
                name="administrative_level_depth_range",
            ),
            models.CheckConstraint(
                condition=models.Q(code__regex=LEVEL_CODE_RE.pattern),
                name="administrative_level_code_shape",
            ),
            models.CheckConstraint(
                condition=~models.Q(label=""),
                name="administrative_level_label",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country_id} {self.code}"


class AdministrativeArea(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="areas")
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    level = models.ForeignKey(
        AdministrativeLevel,
        on_delete=models.PROTECT,
        related_name="areas",
    )
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        db_table = "administrative_area"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "name"],
                condition=models.Q(parent__isnull=True),
                name="admin_area_root_name",
            ),
            models.UniqueConstraint(
                fields=["country", "parent", "name"],
                condition=models.Q(parent__isnull=False),
                name="admin_area_child_name",
            ),
            models.UniqueConstraint(
                fields=["country", "code"],
                condition=~models.Q(code=""),
                name="admin_area_code",
            ),
            models.CheckConstraint(condition=~models.Q(name=""), name="admin_area_name_present"),
            models.CheckConstraint(
                condition=~models.Q(parent_id=models.F("id")),
                name="admin_area_parent_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["country", "parent"], name="admin_area_country_parent"),
        ]

    def __str__(self) -> str:
        return self.name


class Locality(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="localities")
    administrative_area = models.ForeignKey(
        AdministrativeArea,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="localities",
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=32, choices=LocalityKind.CHOICES)

    class Meta:
        db_table = "locality"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["administrative_area", "name", "kind"],
                condition=models.Q(administrative_area__isnull=False),
                name="locality_name_in_area",
            ),
            models.UniqueConstraint(
                fields=["country", "name", "kind"],
                condition=models.Q(administrative_area__isnull=True),
                name="locality_name_in_country",
            ),
            models.CheckConstraint(condition=~models.Q(name=""), name="locality_name_present"),
            models.CheckConstraint(
                condition=models.Q(kind__in=sorted(LocalityKind.VALUES)),
                name="locality_kind_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["country", "name"], name="locality_country_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Address(models.Model):
    """Structured address. A coordinate is stored on the property location, not here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="addresses")
    administrative_area = models.ForeignKey(
        AdministrativeArea,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addresses",
    )
    locality = models.ForeignKey(
        Locality,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addresses",
    )
    street_line = models.CharField(max_length=200, blank=True, default="")
    building_name = models.CharField(max_length=120, blank=True, default="")
    unit_label = models.CharField(max_length=40, blank=True, default="")
    postal_code = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "address"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(administrative_area__isnull=False)
                    | models.Q(locality__isnull=False)
                    | ~models.Q(street_line="")
                    | ~models.Q(building_name="")
                    | ~models.Q(unit_label="")
                    | ~models.Q(postal_code="")
                ),
                name="address_has_a_component",
            ),
        ]

    def __str__(self) -> str:
        return self.street_line or self.country_id
