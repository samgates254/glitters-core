"""Install PostGIS in every database created from this project.

Spatial migrations in later domains must depend on this migration. The
extension binaries come from the PostgreSQL server, not from Python.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS postgis;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
