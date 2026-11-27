"""Static provider catalogue.

A row describes access rules. It does not claim that a live call has succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.spatial.domain import CommercialUse, ProviderServiceStatus


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    official_name: str
    dataset_name: str
    license_identifier: str
    commercial_use: str
    attribution: str
    authentication_type: str
    cost_classification: str
    rate_limit_note: str
    monthly_allowance_note: str
    geographic_coverage: str
    temporal_coverage: str
    resolution_note: str
    freshness_note: str
    service_status: str
    processing_note: str
    caching_policy: str
    catalog_url: str
    check_mode: str
    allowed_host: str
    stac_search: bool = False
    credential_required: bool = False

    def as_row(self) -> dict:
        return {
            "official_name": self.official_name,
            "dataset_name": self.dataset_name,
            "license_identifier": self.license_identifier,
            "commercial_use": self.commercial_use,
            "attribution": self.attribution,
            "authentication_type": self.authentication_type,
            "cost_classification": self.cost_classification,
            "rate_limit_note": self.rate_limit_note,
            "monthly_allowance_note": self.monthly_allowance_note,
            "geographic_coverage": self.geographic_coverage,
            "temporal_coverage": self.temporal_coverage,
            "resolution_note": self.resolution_note,
            "freshness_note": self.freshness_note,
            "service_status": self.service_status,
            "processing_note": self.processing_note,
            "caching_policy": self.caching_policy,
            "catalog_url": self.catalog_url,
            "check_mode": self.check_mode,
            "stac_search": self.stac_search,
            "credential_required": self.credential_required,
            "allowed_host": self.allowed_host,
        }


def _spec(**kwargs) -> ProviderSpec:
    return ProviderSpec(**kwargs)


PROVIDERS: tuple[ProviderSpec, ...] = (
    _spec(
        provider_id="earth-search",
        official_name="Element 84 Earth Search",
        dataset_name="STAC API collections discovered at request time",
        license_identifier="collection-specific",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="Use the license and attribution on the selected STAC collection.",
        authentication_type="none",
        cost_classification="Catalog search does not use an API key. Dataset terms still apply.",
        rate_limit_note="This client sends at most 10 items and a 2-degree bounding box.",
        monthly_allowance_note=(
            "No Glitters quota is configured. The public API is not unlimited infrastructure."
        ),
        geographic_coverage="Where the selected collection has items",
        temporal_coverage="Collection-specific",
        resolution_note="Collection-specific. Do not treat a catalog hit as new ground detail.",
        freshness_note="A catalog hit is not a new parcel image.",
        service_status=ProviderServiceStatus.OPEN_WITH_CONDITIONS,
        processing_note="Metadata search only. Imagery bytes are not downloaded here.",
        caching_policy="A short collection summary may be stored. Imagery bytes are not cached.",
        catalog_url="https://earth-search.aws.element84.com/v1",
        check_mode="stac_root",
        allowed_host="earth-search.aws.element84.com",
        stac_search=True,
    ),
    _spec(
        provider_id="copernicus-stac",
        official_name="Copernicus Data Space Ecosystem",
        dataset_name="STAC catalog at stac.opensearch.dataspace.copernicus.eu",
        license_identifier="Copernicus data policy; confirm the product",
        commercial_use=CommercialUse.CONDITIONS,
        attribution=(
            "Contains modified or unmodified Copernicus Sentinel data when a scene is used."
        ),
        authentication_type="oauth_for_processing_and_download",
        cost_classification=(
            "Processing and download need a registered account and are quota-limited."
        ),
        rate_limit_note="Metadata checks stay small. The processing API is not called.",
        monthly_allowance_note="Account quotas are not configured in this deployment.",
        geographic_coverage="Copernicus archive, including Kenya where scenes exist",
        temporal_coverage="Mission archive. A daily check can return no usable scene.",
        resolution_note=(
            "Sentinel-2 optical bands are 10 m, 20 m, or 60 m. This is not parcel survey detail."
        ),
        freshness_note="Acquisition time is the scene time, not the processing time.",
        service_status=ProviderServiceStatus.AUTHENTICATED,
        processing_note="Catalog metadata only. sh.dataspace.copernicus.eu/process is not called.",
        caching_policy="Discovery summary only.",
        catalog_url="https://stac.opensearch.dataspace.copernicus.eu/v1",
        check_mode="stac_root",
        allowed_host="stac.opensearch.dataspace.copernicus.eu",
        stac_search=True,
    ),
    _spec(
        provider_id="nasa-gibs",
        official_name="NASA Global Imagery Browse Services",
        dataset_name="WMTS EPSG:4326 best",
        license_identifier="NASA Earth data guidelines",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="NASA GIBS. Cite the layer and imagery date returned by the service.",
        authentication_type="none",
        cost_classification=(
            "Browse imagery. Not a paid tile contract and not unlimited infrastructure."
        ),
        rate_limit_note="Health checks read GetCapabilities once and keep only layer names.",
        monthly_allowance_note="No Glitters quota is configured.",
        geographic_coverage="Global browse layers",
        temporal_coverage="Layer-specific",
        resolution_note="Browse resolution is layer-specific. VIIRS browse is hundreds of metres.",
        freshness_note="Use the layer's imagery time. Do not substitute the download time.",
        service_status=ProviderServiceStatus.OPEN_WITH_CONDITIONS,
        processing_note="Capabilities discovery only. Tiles are not proxied.",
        caching_policy="Layer names from the last check may be stored. Tile bytes are not.",
        catalog_url=(
            "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/wmts.cgi"
            "?SERVICE=WMTS&REQUEST=GetCapabilities"
        ),
        check_mode="wmts_capabilities",
        allowed_host="gibs.earthdata.nasa.gov",
    ),
    _spec(
        provider_id="geoboundaries-ken-adm1",
        official_name="geoBoundaries",
        dataset_name="Kenya ADM1 gbOpen",
        license_identifier="Recorded from the provider JSON when a check succeeds",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="geoBoundaries. Keep the license field returned by the API.",
        authentication_type="none",
        cost_classification="Metadata request only. The geometry file is not downloaded.",
        rate_limit_note="One metadata document per check.",
        monthly_allowance_note="No Glitters quota is configured.",
        geographic_coverage="Kenya administrative level 1 where gbOpen publishes it",
        temporal_coverage="Boundary year on the provider record",
        resolution_note="County boundaries are not parcel boundaries and are not a survey.",
        freshness_note=(
            "The boundary year on the record is the vintage. It is not today's title map."
        ),
        service_status=ProviderServiceStatus.OPEN_WITH_CONDITIONS,
        processing_note="Metadata only. gjDownload is not fetched.",
        caching_policy="License, name, source, and year may be stored. Geometry is not.",
        catalog_url="https://www.geoboundaries.org/api/current/gbOpen/KEN/ADM1/",
        check_mode="geoboundaries",
        allowed_host="www.geoboundaries.org",
    ),
    _spec(
        provider_id="overture-stac",
        official_name="Overture Maps",
        dataset_name="Public STAC catalog",
        license_identifier="Theme and release specific. Record it before copying data.",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="Overture Maps Foundation and the upstream sources named on the release.",
        authentication_type="none_for_public_catalog",
        cost_classification="Catalog document only. Cloud object reads are a separate decision.",
        rate_limit_note="The catalog document is read. Child links are not followed.",
        monthly_allowance_note="No GeoParquet ingestion quota is configured.",
        geographic_coverage="Global open map themes, subject to the release",
        temporal_coverage="Release-specific",
        resolution_note=(
            "Building footprints are detections or mapped features, not registered properties."
        ),
        freshness_note="Use the release version. Do not describe it as a live survey.",
        service_status=ProviderServiceStatus.OPEN_WITH_CONDITIONS,
        processing_note="Catalog summary only. GeoParquet is not ingested.",
        caching_policy="Catalog type and link count may be stored.",
        catalog_url="https://stac.overturemaps.org/catalog.json",
        check_mode="json_catalog",
        allowed_host="stac.overturemaps.org",
    ),
    _spec(
        provider_id="nasa-power",
        official_name="NASA POWER",
        dataset_name="Daily point climatology",
        license_identifier="NASA POWER terms",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="NASA POWER when a value is used.",
        authentication_type="none",
        cost_classification="Not called in this release.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No allowance is configured.",
        geographic_coverage="Gridded point estimates, not a site weather station",
        temporal_coverage="Provider archive",
        resolution_note="Grid estimates are not a rain gauge on the parcel.",
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only. The daily point API is not called.",
        caching_policy="No response is cached.",
        catalog_url="https://power.larc.nasa.gov/api/temporal/daily/point",
        check_mode="none",
        allowed_host="power.larc.nasa.gov",
    ),
    _spec(
        provider_id="nasa-firms",
        official_name="NASA FIRMS",
        dataset_name="Active fire and thermal anomaly detections",
        license_identifier="NASA FIRMS / LANCE terms",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="NASA FIRMS. Cite the sensor and observation time.",
        authentication_type="api_key",
        cost_classification="A MAP_KEY is required. None is configured.",
        rate_limit_note="The provider is not contacted.",
        monthly_allowance_note="No key and no allowance are configured.",
        geographic_coverage="Satellite swath detections",
        temporal_coverage="Recent and historical detections when a key is present",
        resolution_note="A thermal detection is not proof that a building or parcel is burning.",
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.AUTHENTICATED,
        processing_note="Registered only. No MAP_KEY is read or sent.",
        caching_policy="No response is cached.",
        catalog_url="https://firms.modaps.eosdis.nasa.gov/api/",
        check_mode="none",
        allowed_host="firms.modaps.eosdis.nasa.gov",
        credential_required=True,
    ),
    _spec(
        provider_id="nasa-imerg",
        official_name="NASA GPM IMERG",
        dataset_name="Integrated Multi-satellitE Retrievals for GPM",
        license_identifier="NASA GPM data policy",
        commercial_use=CommercialUse.UNKNOWN,
        attribution="NASA GPM IMERG when a value is used.",
        authentication_type="not_integrated",
        cost_classification="Not called.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No allowance is configured.",
        geographic_coverage="Near-global precipitation estimates",
        temporal_coverage="Not connected",
        resolution_note="IMERG is a precipitation estimate, not a parcel rain gauge.",
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only.",
        caching_policy="No response is cached.",
        catalog_url="https://gpm.nasa.gov/data/imerg",
        check_mode="none",
        allowed_host="gpm.nasa.gov",
    ),
    _spec(
        provider_id="worldpop",
        official_name="WorldPop",
        dataset_name="Population estimates",
        license_identifier="WorldPop dataset terms",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="WorldPop. Show the data year and resolution.",
        authentication_type="not_integrated",
        cost_classification="Not called.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No allowance is configured.",
        geographic_coverage="Modelled population grids",
        temporal_coverage="Dataset year",
        resolution_note="Modelled population is an estimate, not a census count of the parcel.",
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only. api.worldpop.org is not called.",
        caching_policy="No response is cached.",
        catalog_url="https://api.worldpop.org/v3/",
        check_mode="none",
        allowed_host="api.worldpop.org",
    ),
    _spec(
        provider_id="open-meteo",
        official_name="Open-Meteo",
        dataset_name="Hosted weather API",
        license_identifier="Provider terms for the selected plan",
        commercial_use=CommercialUse.NOT_ELIGIBLE,
        attribution="Do not call the hosted free tier for commercial production without a licence.",
        authentication_type="not_integrated",
        cost_classification="Free tier is not automatically free for commercial production.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No commercial plan is configured.",
        geographic_coverage="Not used",
        temporal_coverage="Not used",
        resolution_note="Not used.",
        freshness_note="Not contacted.",
        service_status=ProviderServiceStatus.FREE_TIER_RESTRICTED,
        processing_note="Registered as restricted. The API is not called.",
        caching_policy="No response is cached.",
        catalog_url="https://api.open-meteo.com/v1/forecast",
        check_mode="none",
        allowed_host="api.open-meteo.com",
    ),
    _spec(
        provider_id="opentopography",
        official_name="OpenTopography",
        dataset_name="Elevation API",
        license_identifier="Depends on the dataset and the account",
        commercial_use=CommercialUse.UNKNOWN,
        attribution="Required by the selected dataset.",
        authentication_type="api_key",
        cost_classification=(
            "Commercial or academic access depends on the subscription. Not configured."
        ),
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No key is configured.",
        geographic_coverage="Dataset-specific",
        temporal_coverage="Dataset-specific",
        resolution_note="Not used. A 30 m DEM would still be screening, not a centimetre survey.",
        freshness_note="Not contacted.",
        service_status=ProviderServiceStatus.COMMERCIAL,
        processing_note="Registered only.",
        caching_policy="No response is cached.",
        catalog_url="https://portal.opentopography.org/apidocs/",
        check_mode="none",
        allowed_host="portal.opentopography.org",
        credential_required=True,
    ),
    _spec(
        provider_id="ardhisasa",
        official_name="Ardhisasa",
        dataset_name="Kenya land registry portal",
        license_identifier="Official registry access is not an open API",
        commercial_use=CommercialUse.NOT_ELIGIBLE,
        attribution="Do not copy registry pages.",
        authentication_type="not_integrated",
        cost_classification="No public API is integrated.",
        rate_limit_note="The site is not scraped.",
        monthly_allowance_note="Not applicable.",
        geographic_coverage="Kenya land records, outside this API",
        temporal_coverage="Not available here",
        resolution_note=(
            "Satellite pixels and OpenStreetMap are not title, ownership, or a legal boundary."
        ),
        freshness_note="Not contacted.",
        service_status=ProviderServiceStatus.UNAVAILABLE,
        processing_note="No adapter. Planning status stays PLANNING STATUS NOT VERIFIED.",
        caching_policy="Nothing is fetched.",
        catalog_url="https://ardhisasa.lands.go.ke/",
        check_mode="none",
        allowed_host="ardhisasa.lands.go.ke",
    ),
    _spec(
        provider_id="nominatim-public",
        official_name="OSM Nominatim public instance",
        dataset_name="Public geocoder",
        license_identifier="ODbL and Nominatim usage policy",
        commercial_use=CommercialUse.NOT_ELIGIBLE,
        attribution="© OpenStreetMap contributors when OSM data is used.",
        authentication_type="not_integrated",
        cost_classification="The public server is not an unlimited commercial geocoder.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="Not used.",
        geographic_coverage="Not used",
        temporal_coverage="Not used",
        resolution_note="Not used.",
        freshness_note="Not contacted.",
        service_status=ProviderServiceStatus.FREE_TIER_RESTRICTED,
        processing_note="Registered so it is not used by accident.",
        caching_policy="No response is cached.",
        catalog_url="https://nominatim.openstreetmap.org/",
        check_mode="none",
        allowed_host="nominatim.openstreetmap.org",
    ),
    _spec(
        provider_id="esa-worldcover",
        official_name="ESA WorldCover",
        dataset_name="Global land-cover maps",
        license_identifier="ESA WorldCover terms for the published year",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="ESA WorldCover. Show the map year.",
        authentication_type="not_integrated",
        cost_classification="Not called.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No product is cached.",
        geographic_coverage="Global classified map",
        temporal_coverage="Published map year, not today's land use",
        resolution_note="Classified pixels are not a current land-use certificate.",
        freshness_note="Not connected. A historical map must not be shown as today's land use.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only.",
        caching_policy="No response is cached.",
        catalog_url="https://esa-worldcover.org/en/data-access",
        check_mode="none",
        allowed_host="esa-worldcover.org",
    ),
    _spec(
        provider_id="copernicus-dem",
        official_name="Copernicus DEM",
        dataset_name="GLO-30 and GLO-90",
        license_identifier="Copernicus DEM terms for the user category",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="Copernicus DEM when a grid is used.",
        authentication_type="not_integrated",
        cost_classification="The access route for this deployment is not verified.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No DEM copy is stored by this release.",
        geographic_coverage="Global",
        temporal_coverage="Product version",
        resolution_note=(
            "GLO-30 is about 30 m. It is screening terrain, not a centimetre site survey."
        ),
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only. No DEM bytes are fetched.",
        caching_policy="No grid is cached.",
        catalog_url="https://dataspace.copernicus.eu/",
        check_mode="none",
        allowed_host="dataspace.copernicus.eu",
    ),
    _spec(
        provider_id="chirps",
        official_name="CHIRPS",
        dataset_name="Climate Hazards Group precipitation",
        license_identifier="UCSB CHIRPS terms",
        commercial_use=CommercialUse.UNKNOWN,
        attribution="CHIRPS when a value is used.",
        authentication_type="not_integrated",
        cost_classification="Not called.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No allowance is configured.",
        geographic_coverage="Quasi-global precipitation estimates",
        temporal_coverage="Not connected",
        resolution_note="A rainfall grid is not a gauge on the parcel.",
        freshness_note="Not connected.",
        service_status=ProviderServiceStatus.NOT_YET_VERIFIED,
        processing_note="Registered only.",
        caching_policy="No response is cached.",
        catalog_url="https://www.chc.ucsb.edu/data",
        check_mode="none",
        allowed_host="www.chc.ucsb.edu",
    ),
    _spec(
        provider_id="google-open-buildings",
        official_name="Google Open Buildings",
        dataset_name="Building detection footprints",
        license_identifier="Dataset licence for the published release",
        commercial_use=CommercialUse.CONDITIONS,
        attribution="Google Open Buildings and the terms of the release.",
        authentication_type="not_integrated",
        cost_classification="Not ingested.",
        rate_limit_note="No request is sent.",
        monthly_allowance_note="No copy is stored.",
        geographic_coverage="Selected African coverage where the dataset publishes it",
        temporal_coverage="Release-specific",
        resolution_note=(
            "A detected footprint is not an official registered property or a measured height."
        ),
        freshness_note="Not ingested.",
        service_status=ProviderServiceStatus.OPEN_WITH_CONDITIONS,
        processing_note="Registered only. Footprints are not loaded.",
        caching_policy="No footprints are cached.",
        catalog_url="https://sites.research.google/gr/open-buildings/",
        check_mode="none",
        allowed_host="sites.research.google",
    ),
)


def provider_by_id(provider_id: str) -> ProviderSpec | None:
    for spec in PROVIDERS:
        if spec.provider_id == provider_id:
            return spec
    return None
