import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("spatial", "0010_spatial_analytics_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="rasterprocessingjob",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="raster_processing_jobs",
                to="organizations.organization",
            ),
        ),
    ]
