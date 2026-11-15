from django.db import migrations


def seed(apps, schema_editor):
    ServiceCategory = apps.get_model("solutions", "ServiceCategory")
    from apps.solutions.domain import SERVICE_CATALOGUE

    for group, slug, name, description in SERVICE_CATALOGUE:
        ServiceCategory.objects.get_or_create(
            slug=slug,
            defaults={"group": group, "name": name, "description": description},
        )


def unseed(apps, schema_editor):
    ServiceCategory = apps.get_model("solutions", "ServiceCategory")
    from apps.solutions.domain import SERVICE_CATALOGUE

    ServiceCategory.objects.filter(slug__in=[row[1] for row in SERVICE_CATALOGUE]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("solutions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
