"""Seed country ladders. Kenya is first, not the only shape."""

from django.db import migrations

COUNTRIES = (
    (
        "KE",
        "KEN",
        "Kenya",
        ((1, "county", "County"), (2, "sub_county", "Sub-county"), (3, "ward", "Ward")),
    ),
    (
        "UG",
        "UGA",
        "Uganda",
        (
            (1, "region", "Region"),
            (2, "district", "District"),
            (3, "county", "County"),
            (4, "sub_county", "Sub-county"),
        ),
    ),
    (
        "TZ",
        "TZA",
        "Tanzania",
        ((1, "region", "Region"), (2, "district", "District"), (3, "ward", "Ward")),
    ),
    (
        "RW",
        "RWA",
        "Rwanda",
        (
            (1, "province", "Province"),
            (2, "district", "District"),
            (3, "sector", "Sector"),
            (4, "cell", "Cell"),
        ),
    ),
)


def seed_countries(apps, schema_editor):
    country_model = apps.get_model("locations", "Country")
    level_model = apps.get_model("locations", "AdministrativeLevel")
    for iso2, iso3, name, levels in COUNTRIES:
        country, _created = country_model.objects.get_or_create(
            iso_alpha2=iso2,
            defaults={"iso_alpha3": iso3, "name": name, "active": True},
        )
        for depth, code, label in levels:
            level_model.objects.get_or_create(
                country=country,
                depth=depth,
                defaults={"code": code, "label": label},
            )


def unseed_countries(apps, schema_editor):
    country_model = apps.get_model("locations", "Country")
    level_model = apps.get_model("locations", "AdministrativeLevel")
    codes = [iso2 for iso2, _iso3, _name, _levels in COUNTRIES]
    level_model.objects.filter(country_id__in=codes).delete()
    country_model.objects.filter(iso_alpha2__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_countries, unseed_countries),
    ]
