"""Management command to ingest satellite imagery (Sentinel-2 L2A) and Copernicus DEM for Mombasa."""

from django.core.management.base import BaseCommand
from apps.spatial.services.imagery_ingestion import (
    ingest_copernicus_dem_mombasa_tile,
    ingest_sentinel2_mombasa_scene,
)


class Command(BaseCommand):
    help = "Ingest Sentinel-2 L2A optical scenes and Copernicus DEM tiles for Mombasa AOI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dem-only",
            action="store_true",
            help="Ingest only the Copernicus DEM dataset.",
        )
        parser.add_argument(
            "--s2-only",
            action="store_true",
            help="Ingest only the Sentinel-2 L2A imagery scenes.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Beginning Earth Observation data ingestion for Mombasa..."))

        if not options["dem_only"]:
            self.stdout.write("Ingesting Sentinel-2 scene S2B_37MER_20260213_0_L2A...")
            s2_item, s2_assets = ingest_sentinel2_mombasa_scene()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully ingested Sentinel-2 scene: {s2_item.item_id} ({len(s2_assets)} assets registered)"
                )
            )

        if not options["s2_only"]:
            self.stdout.write("Ingesting Copernicus DEM GLO-30 tile...")
            dem_item, dem_assets = ingest_copernicus_dem_mombasa_tile()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully ingested Copernicus DEM tile: {dem_item.item_id} ({len(dem_assets)} assets registered)"
                )
            )

        self.stdout.write(self.style.SUCCESS("Earth Observation ingestion completed successfully."))
