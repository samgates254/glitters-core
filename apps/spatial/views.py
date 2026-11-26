"""Views and API viewsets for spatial dataset registry and entities."""

from __future__ import annotations

import json
import logging
import uuid

from django.contrib.gis.geos import Polygon
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.http import FileResponse, Http404, HttpResponse
from rest_framework import exceptions, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.spatial.domain import DatasetVersionStatus, DigitalTwinStatus
from apps.spatial.models import (
    Building,
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ChangeEvidence,
    ChangeReview,
    DatasetRegistry,
    DatasetVersion,
    DigitalTwinAsset,
    DigitalTwinAttributeProvenance,
    DigitalTwinGeneration,
    EntityLineage,
    ImageryCollection,
    ImageryItem,
    LandParcel,
    RasterAsset,
    RasterProcessingJob,
    SpatialAnalysisEvidence,
    SpatialAnalysisProject,
    SpatialAnalysisResult,
    SpatialAnalysisRun,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialScenario,
    SpatialSourceFeature,
)
from apps.spatial.serializers import (
    BuildingSerializer,
    ChangeCandidateSerializer,
    ChangeDetectionProjectSerializer,
    ChangeDetectionRunSerializer,
    ChangeEvidenceSerializer,
    ChangeReviewSerializer,
    DatasetRegistrySerializer,
    DigitalTwinAssetSerializer,
    DigitalTwinGenerationSerializer,
    EntityLineageSerializer,
    ImageryCollectionSerializer,
    ImageryItemSerializer,
    LandParcelSerializer,
    RasterAssetSerializer,
    RasterProcessingJobSerializer,
    SpatialAnalysisEvidenceSerializer,
    SpatialAnalysisProjectSerializer,
    SpatialAnalysisResultSerializer,
    SpatialAnalysisRunSerializer,
    SpatialEntitySerializer,
    SpatialScenarioSerializer,
)
from apps.spatial.services.analytics import execute_spatial_analysis_run
from apps.spatial.services.change_detection import (
    execute_change_detection_pipeline,
    submit_candidate_review,
    validate_acquisitions_compatibility,
)
from apps.spatial.services.lineage import traverse_entity_lineage
from apps.spatial.services.raster_tile_server import render_raster_tile
from apps.spatial.services.request_scope import (
    bind_member_organization,
    hidden_private_urns,
    resolve_request_org_ids,
    scope_member_orgs,
    scope_public_or_member,
)
from apps.spatial.services.safe_paths import (
    UnsafeSpatialPath,
    resolve_raster_file,
    resolve_tile_file,
    resolve_tileset_document,
)
from apps.spatial.services.spectral_service import (
    run_spectral_index_job,
    run_terrain_analysis_job,
)
from apps.spatial.services.stac import (
    search_imagery_items,
    serialize_stac_collection,
    serialize_stac_item,
)
from apps.spatial.services.temporal import (
    get_bi_temporal_slice,
    parse_iso_datetime,
)
from apps.spatial.services.tileset_generator import generate_digital_twin_tileset
from apps.spatial.throttles import SpatialComputeRateThrottle

logger = logging.getLogger(__name__)


def _resolve_request_org_ids(request) -> list[uuid.UUID]:
    """Membership-scoped organization ids. Anonymous callers receive none."""
    return resolve_request_org_ids(request)


def _organization_for_urn(request, urn: str | None):
    """Return the entity organization when the caller is an active member."""
    from apps.organizations.models import Organization

    if not urn:
        return None
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    entity = SpatialEntity.objects.filter(canonical_urn=urn).only("organization_id").first()
    if entity is None or entity.organization_id is None:
        return None
    if entity.organization_id not in _resolve_request_org_ids(request):
        return None
    return Organization.objects.filter(pk=entity.organization_id).first()


class DatasetRegistryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DatasetRegistry.objects.prefetch_related("versions__quality_report").all()
    serializer_class = DatasetRegistrySerializer
    permission_classes = [permissions.AllowAny]

    @action(detail=False, methods=["get"], url_path="layers")
    def layers(self, request):
        """Returns the spatial layer catalogue of available published layers."""
        published_versions = DatasetVersion.objects.filter(
            status=DatasetVersionStatus.PUBLISHED
        ).select_related("dataset")

        layers_data = []
        for v in published_versions:
            extent_coords = None
            if v.spatial_extent:
                extent_coords = json.loads(v.spatial_extent.geojson)

            layers_data.append({
                "id": f"{v.dataset.category.lower()}-{v.version_tag}",
                "name": v.dataset.name,
                "dataset_urn": v.dataset.dataset_urn,
                "version_tag": v.version_tag,
                "category": v.dataset.category,
                "license": v.license_type,
                "attribution": v.attribution_statement,
                "feature_count": v.feature_count,
                "min_zoom": v.min_zoom,
                "max_zoom": v.max_zoom,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "extent": extent_coords,
                "tile_url_template": f"/api/v1/spatial/buildings/tiles/{{z}}/{{x}}/{{y}}/",
            })

        return Response({"layers": layers_data})


class SpatialEntityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SpatialEntitySerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = scope_public_or_member(SpatialEntity.objects.all(), self.request).prefetch_related(
            "external_mappings"
        )
        entity_type = self.request.query_params.get("type")
        if entity_type:
            qs = qs.filter(entity_type=entity_type)
        return qs

    @action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        """Lookup an entity by external provider ID (e.g. ?provider=OSM&id=way/1151899424)."""
        provider = request.query_params.get("provider", "osm")
        external_id = request.query_params.get("id")
        if not provider or not external_id:
            return Response(
                {"error": "Both 'provider' and 'id' query parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entity = (
            scope_public_or_member(SpatialEntity.objects.all(), request)
            .filter(
                external_mappings__provider_name=provider,
                external_mappings__provider_external_id=external_id,
            )
            .first()
        )
        if not entity:
            return Response({"error": "Entity not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(SpatialEntitySerializer(entity).data)

    @action(detail=False, methods=["get"], url_path=r"(?P<urn>[^/]+)/lineage")
    def lineage(self, request, urn=None):
        """Bounded lineage traversal for an entity canonical URN."""
        if not urn:
            return Response(
                {"error": "Entity URN is required in path."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            depth = int(request.query_params.get("depth", 5))
        except (ValueError, TypeError):
            depth = 5

        try:
            lineage_data = traverse_entity_lineage(
                urn,
                organization=_organization_for_urn(request, urn),
                max_depth=depth,
            )
            return Response(lineage_data)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("lineage traversal failed")
            return Response({"error": "Lineage traversal failed."}, status=status.HTTP_400_BAD_REQUEST)


class BuildingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BuildingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = scope_public_or_member(Building.objects.all(), self.request).prefetch_related(
            "external_mappings", "dataset_version"
        )

        # Enforce publication isolation: exclude incomplete (INGESTING), FAILED, or CANCELLED datasets
        qs = qs.exclude(
            dataset_version__status__in=[
                DatasetVersionStatus.INGESTING,
                DatasetVersionStatus.FAILED,
                DatasetVersionStatus.CANCELLED,
            ]
        )

        # Bounding box filter: ?bbox=min_lon,min_lat,max_lon,max_lat
        bbox_param = self.request.query_params.get("bbox")
        if bbox_param:
            try:
                coords = [float(x.strip()) for x in bbox_param.split(",")]
                if len(coords) == 4:
                    bbox_poly = Polygon.from_bbox((coords[0], coords[1], coords[2], coords[3]))
                    bbox_poly.srid = 4326
                    qs = qs.filter(geom__intersects=bbox_poly)
            except (ValueError, TypeError):
                pass

        # Classification filter: ?classification=RESIDENTIAL
        classification = self.request.query_params.get("classification")
        if classification:
            qs = qs.filter(classification=classification.upper())

        # Search filter: ?search=hospital
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                models.Q(name__icontains=search)
                | models.Q(canonical_urn__icontains=search)
                | models.Q(external_mappings__provider_external_id__icontains=search)
            ).distinct()

        # Temporal filtering: ?as_of=... and ?as_was=...
        as_of = self.request.query_params.get("as_of")
        as_was = self.request.query_params.get("as_was")

        if as_of:
            try:
                parse_iso_datetime(as_of)
            except Exception as e:
                raise exceptions.ValidationError({"as_of": str(e)})

        if as_was:
            try:
                parse_iso_datetime(as_was)
            except Exception as e:
                raise exceptions.ValidationError({"as_was": str(e)})

        qs = get_bi_temporal_slice(qs, as_of_time=as_of, as_was_time=as_was)

        return qs

    @action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        """Lookup building by external provider ID, canonical URN, or UUID with full provenance."""
        external_id = request.query_params.get("id")
        provider = request.query_params.get("provider", "osm")

        if not external_id:
            return Response(
                {"error": "The 'id' query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ids_to_try = [external_id]
        if not external_id.startswith("way/") and not external_id.startswith("relation/"):
            ids_to_try.extend([f"way/{external_id}", f"relation/{external_id}"])

        building = (
            scope_public_or_member(Building.objects.all(), request)
            .filter(
                external_mappings__provider_name=provider,
                external_mappings__provider_external_id__in=ids_to_try,
            )
            .select_related("spatialentity_ptr", "dataset_version__dataset")
            .prefetch_related("external_mappings")
            .first()
        )

        if not building:
            # Check canonical URN or UUID
            is_uuid = False
            try:
                uuid.UUID(external_id)
                is_uuid = True
            except ValueError:
                pass

            building = (
                scope_public_or_member(Building.objects.all(), request)
                .filter(
                    models.Q(canonical_urn=external_id)
                    | (models.Q(id=external_id) if is_uuid else models.Q())
                )
                .select_related("spatialentity_ptr", "dataset_version__dataset")
                .prefetch_related("external_mappings")
                .first()
            )

        if not building:
            return Response(
                {"error": f"Building not found for identifier '{external_id}'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Retrieve source feature provenance
        source_feature = (
            SpatialSourceFeature.objects.filter(
                spatial_entity_id=building.id,
                provider_name=provider,
            )
            .first()
        )

        serializer = BuildingSerializer(building)
        data = serializer.data

        # Enrich with source provenance metadata
        if source_feature:
            data["provenance"] = {
                "provider_name": source_feature.provider_name,
                "source_record_id": source_feature.source_record_id,
                "source_version": source_feature.source_version,
                "raw_properties": source_feature.raw_properties,
                "was_repaired": source_feature.was_repaired,
                "repair_details": source_feature.repair_details,
                "captured_at": source_feature.created_at.isoformat() if source_feature.created_at else None,
            }
        else:
            data["provenance"] = None

        if building.dataset_version:
            data["dataset_version"] = {
                "id": str(building.dataset_version.id),
                "dataset_urn": building.dataset_version.dataset.dataset_urn,
                "dataset_name": building.dataset_version.dataset.name,
                "version_tag": building.dataset_version.version_tag,
                "status": building.dataset_version.status,
                "license_type": building.dataset_version.license_type,
                "attribution": building.dataset_version.attribution_statement,
            }
        else:
            data["dataset_version"] = None

        return Response(data)

    @action(detail=False, methods=["get"], url_path=r"(?P<urn>urn:[^/]+)/digital-twin")
    def digital_twin_by_urn(self, request, urn=None):
        """Fetch 3D Digital Twin record and height provenance by canonical URN."""
        building = scope_public_or_member(Building.objects.all(), request).filter(
            canonical_urn=urn
        ).first()
        if not building:
            return Response(
                {"error": f"Building '{urn}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        twin = (
            DigitalTwinAsset.objects.filter(entity=building)
            .prefetch_related("provenance_records")
            .first()
        )
        if not twin:
            return Response(
                {"error": f"No 3D Digital Twin asset generated for '{urn}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DigitalTwinAssetSerializer(twin).data)

    @action(detail=True, methods=["get"], url_path="digital-twin")
    def digital_twin(self, request, pk=None):
        """Fetch 3D Digital Twin record and height provenance by building PK."""
        building = self.get_object()
        twin = (
            DigitalTwinAsset.objects.filter(entity=building)
            .prefetch_related("provenance_records")
            .first()
        )
        if not twin:
            return Response(
                {"error": f"No 3D Digital Twin asset generated for building {pk}."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DigitalTwinAssetSerializer(twin).data)

    @action(
        detail=False,
        methods=["get"],
        url_path=r"tiles/(?P<z>[0-9]+)/(?P<x>[0-9]+)/(?P<y>[0-9]+)",
    )
    def tiles(self, request, z=None, x=None, y=None):
        """
        Serves high-performance dynamic Mapbox Vector Tiles (.mvt) generated natively in PostGIS.
        """
        try:
            zoom = int(z)
            tile_x = int(x)
            tile_y = int(y)
        except (ValueError, TypeError):
            return Response({"error": "Invalid tile coordinates."}, status=status.HTTP_400_BAD_REQUEST)

        org_ids = [str(org_id) for org_id in _resolve_request_org_ids(request)]
        # An empty Python list becomes an untyped Postgres array and fails ANY().
        if org_ids:
            organization_sql = "(e.organization_id IS NULL OR e.organization_id::text = ANY(%s))"
        else:
            organization_sql = "e.organization_id IS NULL"

        as_of = request.query_params.get("as_of")
        as_was = request.query_params.get("as_was")

        temporal_clauses = []
        sql_params = [zoom, tile_x, tile_y]
        if org_ids:
            sql_params.append(org_ids)

        if as_was:
            try:
                dt_was = parse_iso_datetime(as_was)
                temporal_clauses.append("e.created_at <= %s AND (e.superseded_at IS NULL OR e.superseded_at > %s)")
                sql_params.extend([dt_was, dt_was])
            except Exception:
                return Response({"error": "Invalid 'as_was' timestamp format."}, status=status.HTTP_400_BAD_REQUEST)

        if as_of:
            try:
                dt_of = parse_iso_datetime(as_of)
                temporal_clauses.append("e.valid_from <= %s AND (e.valid_to IS NULL OR e.valid_to > %s)")
                sql_params.extend([dt_of, dt_of])
                if not as_was:
                    temporal_clauses.append("e.superseded_at IS NULL")
            except Exception:
                return Response({"error": "Invalid 'as_of' timestamp format."}, status=status.HTTP_400_BAD_REQUEST)

        if not as_of and not as_was:
            temporal_clauses.append("e.superseded_at IS NULL AND (e.valid_to IS NULL OR e.valid_to > clock_timestamp())")

        temporal_sql = " AND ".join(temporal_clauses)

        # Build dynamic PostGIS MVT query
        sql = f"""
        WITH tile_bounds AS (
            SELECT ST_TileEnvelope(%s, %s, %s) AS geom_3857
        ),
        mvt_features AS (
            SELECT
                b.spatialentity_ptr_id AS id,
                e.canonical_urn,
                b.name,
                b.classification,
                COALESCE(b.height_m, 0.0) AS height_m,
                COALESCE(b.levels_above_ground, 1) AS levels,
                COALESCE(b.footprint_area_sqm, 0.0) AS footprint_area_sqm,
                COALESCE(m.provider_external_id, '') AS osm_id,
                ST_AsMVTGeom(
                    ST_Transform(b.geom, 3857),
                    tb.geom_3857,
                    4096,
                    64,
                    true
                ) AS mvt_geom
            FROM spatial_building b
            JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
            JOIN spatial_dataset_version v ON e.dataset_version_id = v.id
            LEFT JOIN spatial_identity_mapping m ON m.entity_id = e.id
            CROSS JOIN tile_bounds tb
            WHERE v.status = 'PUBLISHED'
              AND {organization_sql}
              AND {temporal_sql}
              AND ST_Intersects(ST_Transform(b.geom, 3857), tb.geom_3857)
        )
        SELECT ST_AsMVT(mvt_features.*, 'buildings', 4096, 'mvt_geom') FROM mvt_features;
        """

        with connection.cursor() as cursor:
            cursor.execute(sql, sql_params)
            tile_data = cursor.fetchone()[0]

        tile_bytes = bytes(tile_data) if tile_data else b""
        response = HttpResponse(tile_bytes, content_type="application/vnd.mapbox-vector-tile")
        response["Cache-Control"] = "public, max-age=86400, s-maxage=86400, stale-while-revalidate=604800"
        return response


class LandParcelViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LandParcelSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        return scope_public_or_member(LandParcel.objects.all(), self.request).prefetch_related(
            "external_mappings"
        )


class EntityLineageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EntityLineage.objects.all()
    serializer_class = EntityLineageSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        hidden = hidden_private_urns(self.request)
        qs = super().get_queryset().exclude(predecessor_urn__in=hidden).exclude(
            successor_urn__in=hidden
        )
        urn = self.request.query_params.get("urn")
        if urn:
            qs = qs.filter(predecessor_urn=urn) | qs.filter(successor_urn=urn)
        return qs

    @action(detail=False, methods=["get"], url_path=r"tree/(?P<urn>[^/]+)")
    def tree(self, request, urn=None):
        """Bounded lineage traversal tree for an entity canonical URN."""
        target_urn = urn or request.query_params.get("urn")
        if not target_urn:
            return Response(
                {"error": "The 'urn' parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            depth = int(request.query_params.get("depth", 5))
        except (ValueError, TypeError):
            depth = 5

        try:
            tree_data = traverse_entity_lineage(
                target_urn,
                organization=_organization_for_urn(request, target_urn),
                max_depth=depth,
            )
            return Response(tree_data)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("lineage traversal failed")
            return Response({"error": "Lineage traversal failed."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="tree")
    def tree_query(self, request):
        """Query-param variant: GET /api/v1/spatial/lineage/tree/?urn=..."""
        return self.tree(request, urn=request.query_params.get("urn"))


class DigitalTwinViewSet(viewsets.ModelViewSet):
    """Authenticated 3D Tiles API. Stored paths cannot leave the tileset root."""

    queryset = DigitalTwinGeneration.objects.all()
    serializer_class = DigitalTwinGenerationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_throttles(self):
        if self.action == "generate":
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        org_ids = _resolve_request_org_ids(self.request)
        foreign_private = DigitalTwinAsset.objects.filter(
            generation_id=models.OuterRef("pk"),
            entity__organization__isnull=False,
        ).exclude(entity__organization_id__in=org_ids)
        return qs.exclude(models.Exists(foreign_private))

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        """Rebuild a tileset for buildings the caller is allowed to see inside bbox."""
        bbox = request.data.get("bbox")
        try:
            limit = int(request.data.get("limit", 500))
        except (TypeError, ValueError):
            return Response({"error": "limit must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        limit = max(1, min(limit, 500))
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return Response(
                {"error": "bbox is required and must be [min_lon, min_lat, max_lon, max_lat]."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            poly = Polygon.from_bbox(tuple(float(value) for value in bbox))
        except (TypeError, ValueError):
            return Response({"error": "bbox values must be numeric."}, status=status.HTTP_400_BAD_REQUEST)
        poly.srid = 4326
        qs = scope_public_or_member(Building.objects.all(), request).filter(
            geom__isnull=False,
            geom__intersects=poly,
        )[:limit]
        if not qs.exists():
            return Response(
                {"error": "No building footprints matched the specified envelope."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info("digital twin generate requested by user %s", request.user.pk)
        try:
            gen = generate_digital_twin_tileset(qs)
        except Exception:
            logger.exception("digital twin generation failed")
            return Response(
                {"error": "Digital twin generation failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            DigitalTwinGenerationSerializer(gen).data,
            status=status.HTTP_201_CREATED,
        )

    def _tileset_file(self, generation):
        try:
            return resolve_tileset_document(generation.tileset_path)
        except UnsafeSpatialPath:
            logger.warning("rejected tileset path for generation %s", generation.id)
            raise Http404("Tileset file not found on disk.") from None

    @action(detail=True, methods=["get"], url_path="tileset")
    def tileset(self, request, pk=None):
        """Serve tileset.json from the configured tileset root."""
        generation = self.get_object()
        tileset_data = self._tileset_file(generation).read_text(encoding="utf-8")
        response = HttpResponse(tileset_data, content_type="application/json")
        response["Cache-Control"] = "private, max-age=3600"
        return response

    @action(detail=True, methods=["get"], url_path=r"tiles/(?P<tile_name>[^/]+)")
    def tile(self, request, pk=None, tile_name=None):
        """Serve one GLB tile stored beside a contained tileset."""
        generation = self.get_object()
        try:
            tile_path = resolve_tile_file(generation.tileset_path, tile_name or "")
        except UnsafeSpatialPath:
            logger.warning("rejected tile path for generation %s", generation.id)
            return Response({"error": "Tile not found."}, status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(tile_path.open("rb"), content_type="model/gltf-binary")
        response["Cache-Control"] = "private, max-age=86400"
        return response

    @action(detail=False, methods=["get"], url_path="active-tileset")
    def active_tileset(self, request):
        """Return the latest visible completed tileset. This GET does not generate one."""
        generations = self.get_queryset().filter(status=DigitalTwinStatus.COMPLETED).order_by("-created_at")
        for generation in generations.iterator():
            try:
                path = resolve_tileset_document(generation.tileset_path)
            except UnsafeSpatialPath:
                continue
            tileset_json = json.loads(path.read_text(encoding="utf-8"))
            content = tileset_json.get("root", {}).get("content")
            if isinstance(content, dict):
                content["uri"] = f"/api/v1/spatial/digital-twins/{generation.id}/tiles/tile_0.glb"
            response = HttpResponse(json.dumps(tileset_json), content_type="application/json")
            response["Cache-Control"] = "private, max-age=3600"
            return response
        raise Http404("No completed tileset is available.")


def spatial_health_check(request):
    """Spatial platform health check verifying PostGIS, published datasets, and spatial counts."""
    from django.http import JsonResponse
    with connection.cursor() as cursor:
        cursor.execute("SELECT postgis_version();")
        postgis_ver = cursor.fetchone()[0]

    published_count = Building.objects.filter(
        dataset_version__status=DatasetVersionStatus.PUBLISHED
    ).count()

    return JsonResponse({
        "status": "healthy",
        "postgis_version": postgis_ver,
        "published_building_count": published_count,
        "database_engine": "PostgreSQL 16 + PostGIS 3.5",
    })


class ImageryCollectionViewSet(viewsets.ReadOnlyModelViewSet):
    """STAC and REST ViewSet for Earth Observation Imagery Collections."""
    queryset = ImageryCollection.objects.filter(is_active=True).order_by("collection_id")
    serializer_class = ImageryCollectionSerializer
    permission_classes = [permissions.AllowAny]
    lookup_value_regex = r"[^/]+"

    def get_object(self):
        lookup = self.kwargs.get("pk")
        try:
            return ImageryCollection.objects.get(id=uuid.UUID(str(lookup)))
        except (ImageryCollection.DoesNotExist, ValueError):
            try:
                return ImageryCollection.objects.get(collection_id=lookup)
            except ImageryCollection.DoesNotExist:
                raise Http404(f"ImageryCollection '{lookup}' not found.")

    @action(detail=True, methods=["get"], url_path="stac")
    def stac(self, request, pk=None):
        collection = self.get_object()
        base_url = request.build_absolute_uri("/")[:-1]
        stac_doc = serialize_stac_collection(collection, base_url=base_url)
        return Response(stac_doc, content_type="application/json")

    @action(detail=True, methods=["get"], url_path="items")
    def items(self, request, pk=None):
        collection = self.get_object()
        base_url = request.build_absolute_uri("/")[:-1]
        qs = collection.items.prefetch_related("assets").all()
        features = [serialize_stac_item(item, base_url=base_url) for item in qs]
        return Response(
            {
                "type": "FeatureCollection",
                "features": features,
            },
            content_type="application/geo+json",
        )


class ImageryItemViewSet(viewsets.ReadOnlyModelViewSet):
    """STAC and REST ViewSet for Earth Observation Imagery Scenes and Acquisitions."""
    queryset = ImageryItem.objects.select_related("collection").prefetch_related("assets").order_by("-acquisition_datetime")
    serializer_class = ImageryItemSerializer
    permission_classes = [permissions.AllowAny]
    lookup_value_regex = r"[^/]+"

    def get_object(self):
        lookup = self.kwargs.get("pk")
        try:
            return ImageryItem.objects.get(id=uuid.UUID(str(lookup)))
        except (ImageryItem.DoesNotExist, ValueError):
            try:
                return ImageryItem.objects.get(item_id=lookup)
            except ImageryItem.DoesNotExist:
                try:
                    return ImageryItem.objects.get(canonical_urn=lookup)
                except ImageryItem.DoesNotExist:
                    raise Http404(f"ImageryItem '{lookup}' not found.")

    @action(detail=True, methods=["get"], url_path="stac")
    def stac(self, request, pk=None):
        item = self.get_object()
        base_url = request.build_absolute_uri("/")[:-1]
        stac_doc = serialize_stac_item(item, base_url=base_url)
        return Response(stac_doc, content_type="application/geo+json")

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        """STAC Search endpoint supporting bbox, collections, datetime, and cloud cover filtering."""
        bbox_raw = request.query_params.get("bbox")
        bbox = None
        if bbox_raw:
            try:
                bbox = [float(x.strip()) for x in bbox_raw.split(",")]
            except (ValueError, TypeError):
                return Response({"error": "Invalid bbox format. Expected 'min_lon,min_lat,max_lon,max_lat'"}, status=400)

        collections = request.query_params.getlist("collections")
        if not collections and request.query_params.get("collection"):
            collections = [request.query_params.get("collection")]

        cloud_cover_raw = request.query_params.get("max_cloud_cover")
        cloud_cover = float(cloud_cover_raw) if cloud_cover_raw else None

        limit = min(int(request.query_params.get("limit", 50)), 100)

        items_qs = search_imagery_items(
            bbox=bbox,
            collection_ids=collections if collections else None,
            max_cloud_cover=cloud_cover,
            limit=limit,
        )

        base_url = request.build_absolute_uri("/")[:-1]
        features = [serialize_stac_item(item, base_url=base_url) for item in items_qs]
        return Response(
            {
                "type": "FeatureCollection",
                "features": features,
            },
            content_type="application/geo+json",
        )


class RasterAssetViewSet(viewsets.ReadOnlyModelViewSet):
    """Serves metadata, raw COG downloads, and dynamic Web Mercator XYZ PNG tiles."""
    queryset = RasterAsset.objects.select_related("item").all().order_by("-created_at")
    serializer_class = RasterAssetSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        org_ids = _resolve_request_org_ids(self.request) if user and user.is_authenticated else []
        evidence = ChangeEvidence.objects.filter(derived_diff_asset__isnull=False)
        if org_ids:
            evidence = evidence.exclude(candidate__run__project__organization_id__in=org_ids)
        qs = qs.exclude(id__in=evidence.values_list("derived_diff_asset_id", flat=True))

        asset_key = self.request.query_params.get("asset_key")
        if asset_key:
            qs = qs.filter(asset_key=asset_key)
        asset_type = self.request.query_params.get("asset_type")
        if asset_type:
            qs = qs.filter(asset_type=asset_type)
        item_id = self.request.query_params.get("item_id")
        if item_id:
            qs = qs.filter(models.Q(item__item_id=item_id) | models.Q(item__id=item_id))
        return qs

    def _assert_raster_access(self, asset, request):
        evidence_orgs = list(
            ChangeEvidence.objects.filter(
                models.Q(derived_diff_asset=asset)
            ).values_list("candidate__run__project__organization_id", flat=True).distinct()
        )
        if evidence_orgs:
            user = request.user
            if not user or not user.is_authenticated:
                raise exceptions.PermissionDenied("Authentication required to access organization raster products.")
            user_org_ids = _resolve_request_org_ids(request)
            if not any(oid in user_org_ids for oid in evidence_orgs):
                raise exceptions.PermissionDenied("Cross-organization access to raster product denied.")

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        asset = self.get_object()
        self._assert_raster_access(asset, request)
        try:
            file_path = resolve_raster_file(asset.storage_path)
        except UnsafeSpatialPath as exc:
            raise Http404("Raster file not found.") from exc

        response = FileResponse(file_path.open("rb"), content_type=asset.mime_type)
        response["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
        return response

    def tile(self, request, pk=None, z=None, x=None, y=None, asset=None):
        """Serves 256x256 Web Mercator PNG tile for this RasterAsset."""
        if asset is None:
            asset = self.get_object()
        self._assert_raster_access(asset, request)
        try:
            resolve_raster_file(asset.storage_path)
        except UnsafeSpatialPath as exc:
            raise Http404("Raster file not found.") from exc
        try:
            z_int = int(z)
            x_int = int(x)
            # Support optional .png in y coordinate
            y_str = str(y).replace(".png", "")
            y_int = int(y_str)
        except (ValueError, TypeError):
            return Response({"error": "Invalid tile coordinates"}, status=400)

        png_bytes = render_raster_tile(asset, z=z_int, x=x_int, y=y_int)
        response = HttpResponse(png_bytes, content_type="image/png")
        response["Cache-Control"] = "public, max-age=86400, immutable"
        return response

    @action(
        detail=False,
        methods=["get"],
        url_path=r"by-key/(?P<asset_key>[^/]+)/tiles/(?P<z>\d+)/(?P<x>\d+)/(?P<y>[^/]+)",
    )
    def tile_by_key(self, request, asset_key=None, z=None, x=None, y=None):
        """Serves tile for the latest RasterAsset matching asset_key (e.g. visual, ndvi, hillshade)."""
        asset = (
            self.get_queryset()
            .filter(asset_key=asset_key.lower())
            .order_by("-created_at")
            .first()
        )
        if not asset:
            raise Http404(f"No raster asset found for key '{asset_key}'.")
        return self.tile(request, pk=asset.id, z=z, x=x, y=y, asset=asset)


class RasterProcessingJobViewSet(viewsets.ModelViewSet):
    """Authenticated raster jobs scoped to the caller's organizations."""
    queryset = RasterProcessingJob.objects.select_related(
        "input_item", "output_asset", "organization"
    ).all().order_by("-created_at")
    serializer_class = RasterProcessingJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.action == "create":
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return scope_member_orgs(super().get_queryset(), self.request, "organization_id")

    def create(self, request, *args, **kwargs):
        from apps.organizations.models import Organization

        org_ids = _resolve_request_org_ids(request)
        if len(org_ids) != 1:
            return Response(
                {"error": "organization_id must select one of your organizations."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        organization = Organization.objects.get(pk=org_ids[0])
        item_id = request.data.get("item_id")
        operation = str(request.data.get("operation", "ndvi")).lower()
        parameters = request.data.get("parameters", {})

        if not item_id:
            return Response({"error": "item_id is required."}, status=400)

        try:
            try:
                item = ImageryItem.objects.get(id=uuid.UUID(str(item_id)))
            except (ValueError, TypeError):
                item = ImageryItem.objects.get(item_id=item_id)
        except ImageryItem.DoesNotExist:
            try:
                item = ImageryItem.objects.get(canonical_urn=item_id)
            except ImageryItem.DoesNotExist:
                return Response({"error": f"ImageryItem '{item_id}' not found."}, status=404)

        try:
            if operation in ("ndvi", "ndwi", "ndbi", "savi"):
                job = run_spectral_index_job(item, index_name=operation, parameters=parameters)
            elif operation in ("hillshade", "slope", "terrain"):
                job = run_terrain_analysis_job(item, parameters=parameters)
            else:
                return Response(
                    {"error": f"Unsupported operation '{operation}'. Supported: ndvi, ndwi, ndbi, savi, hillshade, slope."},
                    status=400,
                )

            if job.organization_id != organization.id:
                job.organization = organization
                job.save(update_fields=["organization", "updated_at"])
            serializer = self.get_serializer(job)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception:
            logger.exception("raster processing failed")
            return Response({"error": "Raster processing failed."}, status=500)


class ChangeDetectionProjectViewSet(viewsets.ModelViewSet):
    """API for managing Change Detection Projects with organization isolation."""
    queryset = ChangeDetectionProject.objects.select_related("organization").all().order_by("-created_at")
    serializer_class = ChangeDetectionProjectSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.action == "create_run":
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = scope_member_orgs(super().get_queryset(), self.request, "organization_id")
        org_param = self.request.query_params.get("organization") or self.request.query_params.get("organization_id")
        if org_param:
            try:
                org_uuid = uuid.UUID(org_param)
                qs = qs.filter(organization_id=org_uuid)
            except ValueError:
                qs = qs.filter(organization__slug=org_param)
        return qs

    def perform_create(self, serializer):
        bind_member_organization(self.request, serializer)

    @action(detail=True, methods=["post"], url_path="runs")
    def create_run(self, request, pk=None):
        project = self.get_object()
        baseline_id = request.data.get("baseline_item_id")
        comparison_id = request.data.get("comparison_item_id")
        cva_threshold = float(request.data.get("cva_threshold", 0.22))
        min_area = float(request.data.get("min_change_area_m2", 150.0))

        if not baseline_id or not comparison_id:
            return Response(
                {"error": "baseline_item_id and comparison_item_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            baseline = ImageryItem.objects.filter(models.Q(id__in=[baseline_id] if len(baseline_id) == 36 else []) | models.Q(item_id=baseline_id)).first()
            comparison = ImageryItem.objects.filter(models.Q(id__in=[comparison_id] if len(comparison_id) == 36 else []) | models.Q(item_id=comparison_id)).first()
            if not baseline or not comparison:
                return Response({"error": "Baseline or comparison ImageryItem not found."}, status=status.HTTP_404_NOT_FOUND)

            run = ChangeDetectionRun.objects.create(
                project=project,
                baseline_item=baseline,
                comparison_item=comparison,
                algorithm_identifier=str(request.data.get("algorithm_identifier", "Seasonally_Detrended_Change_Vector_Analysis_CVA")),
                parameters={"cva_threshold": cva_threshold, "min_change_area_m2": min_area},
                created_by=request.user if request.user.is_authenticated else None,
            )

            execute_change_detection_pipeline(run, cva_threshold=cva_threshold, min_change_area_m2=min_area)
            serializer = ChangeDetectionRunSerializer(run)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception:
            logger.exception("change detection run failed")
            return Response(
                {"error": "Change detection run failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get"], url_path="runs")
    def list_runs(self, request, pk=None):
        project = self.get_object()
        runs = project.runs.select_related("baseline_item", "comparison_item").all().order_by("-created_at")
        serializer = ChangeDetectionRunSerializer(runs, many=True)
        return Response(serializer.data)


class ChangeDetectionRunViewSet(viewsets.ReadOnlyModelViewSet):
    """API for inspecting change detection runs, candidates, and statistical summaries."""
    queryset = ChangeDetectionRun.objects.select_related("project", "baseline_item", "comparison_item").all().order_by("-created_at")
    serializer_class = ChangeDetectionRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return scope_member_orgs(super().get_queryset(), self.request, "project__organization_id")

    @action(detail=True, methods=["get"], url_path="candidates")
    def candidates(self, request, pk=None):
        run = self.get_object()
        qs = run.candidates.select_related("associated_building").prefetch_related("evidence", "reviews").all()

        # Filtering
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        review_status = request.query_params.get("review_status")
        if review_status:
            qs = qs.filter(review_status=review_status)
        min_conf = request.query_params.get("min_confidence")
        if min_conf:
            qs = qs.filter(detection_confidence__gte=float(min_conf))

        if request.query_params.get("format") == "geojson":
            features = []
            for c in qs:
                features.append({
                    "type": "Feature",
                    "id": str(c.id),
                    "geometry": json.loads(c.geometry.geojson) if c.geometry else None,
                    "properties": {
                        "candidate_id": c.candidate_id,
                        "category": c.category,
                        "confidence": float(c.detection_confidence),
                        "area_sqm": float(c.area_sqm),
                        "review_status": c.review_status,
                        "associated_building_urn": c.associated_building.canonical_urn if c.associated_building else None,
                        "association_type": c.building_association_type,
                    },
                })
            return Response({"type": "FeatureCollection", "features": features})

        serializer = ChangeCandidateSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="summary")
    def summary(self, request, pk=None):
        run = self.get_object()
        candidates = run.candidates.all()

        category_counts = {}
        for c in candidates:
            category_counts[c.category] = category_counts.get(c.category, 0) + 1

        review_counts = {}
        for c in candidates:
            review_counts[c.review_status] = review_counts.get(c.review_status, 0) + 1

        total_area = sum(float(c.area_sqm) for c in candidates)
        avg_confidence = (
            sum(float(c.detection_confidence) for c in candidates) / len(candidates)
            if candidates.exists()
            else 0.0
        )

        return Response({
            "run_id": str(run.id),
            "status": run.status,
            "total_candidates": candidates.count(),
            "total_change_area_sqm": total_area,
            "average_confidence": round(avg_confidence, 4),
            "category_breakdown": category_counts,
            "review_breakdown": review_counts,
            "processing_duration_sec": run.processing_duration_sec,
        })


class ChangeCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    """API for querying individual change candidates, evidence dossiers, and submitting human reviews."""
    queryset = ChangeCandidate.objects.select_related("run", "associated_building").prefetch_related("evidence", "reviews").all().order_by("-detection_confidence", "-area_sqm")
    serializer_class = ChangeCandidateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = scope_member_orgs(
            super().get_queryset(), self.request, "run__project__organization_id"
        )

        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        review_status = self.request.query_params.get("review_status")
        if review_status:
            qs = qs.filter(review_status=review_status)
        run_id = self.request.query_params.get("run_id") or self.request.query_params.get("run")
        if run_id:
            qs = qs.filter(run_id=run_id)
        project_id = self.request.query_params.get("project_id") or self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(run__project_id=project_id)
        return qs

    @action(detail=True, methods=["post"], url_path="review")
    def review(self, request, pk=None):
        candidate = self.get_object()
        decision = request.data.get("decision")
        comments = request.data.get("comments") or request.data.get("notes", "")

        if not decision:
            return Response({"error": "decision is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            review_record = submit_candidate_review(
                candidate=candidate,
                decision=decision,
                comments=comments,
                reviewer=request.user if request.user.is_authenticated else None,
            )
            serializer = ChangeReviewSerializer(review_record)
            candidate.refresh_from_db()
            return Response(
                {
                    "candidate_id": str(candidate.id),
                    "candidate_status": candidate.review_status,
                    "review": serializer.data,
                },
                status=status.HTTP_200_OK,
            )
        except ValidationError as err:
            return Response({"error": str(err)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("change candidate review failed")
            return Response(
                {"error": "Change candidate review failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"], url_path="evidence")
    def evidence(self, request, pk=None):
        candidate = self.get_object()
        serializer = ChangeEvidenceSerializer(candidate.evidence.all(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="geojson")
    def geojson(self, request):
        qs = self.filter_queryset(self.get_queryset())
        features = []
        for c in qs[:250]:
            features.append({
                "type": "Feature",
                "id": str(c.id),
                "geometry": json.loads(c.geometry.geojson) if c.geometry else None,
                "properties": {
                    "candidate_id": c.candidate_id,
                    "category": c.category,
                    "confidence": float(c.detection_confidence),
                    "area_sqm": float(c.area_sqm),
                    "review_status": c.review_status,
                    "associated_building_urn": c.associated_building.canonical_urn if c.associated_building else None,
                    "association_type": c.building_association_type,
                    "overlap_ratio": c.building_overlap_ratio,
                    "distance_m": c.building_distance_m,
                },
            })
        return Response({"type": "FeatureCollection", "features": features})


class SpatialAnalysisProjectViewSet(viewsets.ModelViewSet):
    """API for managing analytical projects across bounded AOIs."""
    queryset = SpatialAnalysisProject.objects.select_related("organization").prefetch_related("runs").all().order_by("-created_at")
    serializer_class = SpatialAnalysisProjectSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = scope_member_orgs(super().get_queryset(), self.request, "organization_id")
        org_param = self.request.query_params.get("organization") or self.request.query_params.get("organization_id")
        if org_param:
            try:
                org_uuid = uuid.UUID(org_param)
                qs = qs.filter(organization_id=org_uuid)
            except ValueError:
                qs = qs.filter(organization__slug=org_param)
        return qs

    def perform_create(self, serializer):
        bind_member_organization(self.request, serializer)


class SpatialAnalysisRunViewSet(viewsets.ModelViewSet):
    """API for configuring, executing, and monitoring spatial analytical pipeline runs."""
    queryset = SpatialAnalysisRun.objects.select_related("project", "model_version").prefetch_related("results").all().order_by("-created_at")
    serializer_class = SpatialAnalysisRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.action == "execute":
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = scope_member_orgs(super().get_queryset(), self.request, "project__organization_id")

        project_id = self.request.query_params.get("project_id") or self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        analysis_type = self.request.query_params.get("analysis_type")
        if analysis_type:
            qs = qs.filter(analysis_type=analysis_type)
        return qs

    def perform_create(self, serializer):
        project = serializer.validated_data.get("project")
        if project is not None and project.organization_id not in _resolve_request_org_ids(self.request):
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="execute")
    def execute(self, request, pk=None):
        run = self.get_object()
        try:
            executed_run = execute_spatial_analysis_run(run)
            serializer = self.get_serializer(executed_run)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception:
            logger.exception("spatial analysis execution failed")
            return Response(
                {"error": "Spatial analysis execution failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, pk=None):
        run = self.get_object()
        results_qs = run.results.all()
        serializer = SpatialAnalysisResultSerializer(results_qs, many=True)
        return Response(serializer.data)


class SpatialAnalysisResultViewSet(viewsets.ReadOnlyModelViewSet):
    """API for querying individual spatial intelligence results and dossiers."""
    queryset = SpatialAnalysisResult.objects.select_related("run", "associated_building", "raster_asset").prefetch_related("evidence").all().order_by("-confidence", "-score")
    serializer_class = SpatialAnalysisResultSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = scope_member_orgs(
            super().get_queryset(), self.request, "run__project__organization_id"
        )

        run_id = self.request.query_params.get("run_id") or self.request.query_params.get("run")
        if run_id:
            qs = qs.filter(run_id=run_id)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        label = self.request.query_params.get("evidence_label") or self.request.query_params.get("label")
        if label:
            qs = qs.filter(evidence_label=label)
        return qs

    @action(detail=True, methods=["get"], url_path="evidence")
    def evidence(self, request, pk=None):
        result = self.get_object()
        serializer = SpatialAnalysisEvidenceSerializer(result.evidence.all(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="geojson")
    def geojson(self, request):
        qs = self.filter_queryset(self.get_queryset())
        features = []
        for r in qs[:500]:
            features.append({
                "type": "Feature",
                "id": str(r.id),
                "geometry": json.loads(r.geometry.geojson) if r.geometry else None,
                "properties": {
                    "result_id": r.result_id,
                    "category": r.category,
                    "evidence_label": r.evidence_label,
                    "score": r.score,
                    "confidence": float(r.confidence),
                    "associated_building_urn": r.associated_building.canonical_urn if r.associated_building else None,
                    "metrics": r.metrics_payload,
                },
            })
        return Response({"type": "FeatureCollection", "features": features})


class SpatialScenarioViewSet(viewsets.ModelViewSet):
    """API for creating, evaluating, and comparing spatial intelligence scenarios."""
    queryset = SpatialScenario.objects.select_related("project", "baseline_run", "comparison_run").all().order_by("-created_at")
    serializer_class = SpatialScenarioSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = scope_member_orgs(super().get_queryset(), self.request, "project__organization_id")

        project_id = self.request.query_params.get("project_id") or self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def perform_create(self, serializer):
        project = serializer.validated_data.get("project")
        if project is not None and project.organization_id not in _resolve_request_org_ids(self.request):
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["get"], url_path="compare")
    def compare(self, request, pk=None):
        scenario = self.get_object()
        baseline_summary = scenario.baseline_run.summary_metrics or {}
        comparison_summary = scenario.comparison_run.summary_metrics or {}
        delta = {
            "baseline_results_count": scenario.baseline_run.total_results_count,
            "comparison_results_count": scenario.comparison_run.total_results_count,
            "baseline_summary": baseline_summary,
            "comparison_summary": comparison_summary,
        }
        return Response(delta)
