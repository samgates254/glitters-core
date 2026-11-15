"""Enable btree_gist extension in PostgreSQL.

Required for composite GiST exclusion constraints on (UUID, daterange).
"""

from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_enable_postgis"),
    ]

    operations = [
        BtreeGistExtension(),
    ]
