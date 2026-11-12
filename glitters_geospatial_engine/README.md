# `glitters_geospatial_engine` (v1.1.0 — Audit-Remediated Release)

Isolated Python package implementing computational geometry, planar topology, parcel intelligence
with cryptographic cadastral evidence verification, multi-paradigm spatial indexing, affine-aware
satellite imagery resampling & NoData-safe spectral indices, resolution-gated building & land-cover
detection, seasonally detrended change detection with exact connected-component polygonization,
DEM hydrology & flood screening, road-network routing (Dijkstra, A*, Contraction Hierarchies, MSSP,
and corridor-buffered isochrones), analytical/Monte Carlo uncertainty propagation, and GLITTERS CORE
integration contracts for Kenya (`EPSG:4326`, `EPSG:4210`, `EPSG:32636`, `EPSG:32637`, `EPSG:32736`,
`EPSG:32737`, `EPSG:21096`, `EPSG:21097`, `EPSG:21036`, `EPSG:21037`).

## Installation & Testing

```bash
python3 -m pip install --no-build-isolation --no-deps --target /tmp/install .
PYTHONPATH=. python3 tests/test_glitters_geospatial_engine.py
PYTHONPATH=. python3 benchmarks/run_benchmarks.py
```
