# Sentinel-1 InSAR Processing Platform

**A Python project for building reproducible Sentinel-1 SAR acquisition, geometry-validation, and InSAR processing workflows.**

## Objective

The objective of this project is to develop a modular foundation for processing Sentinel-1 Single Look Complex (SLC) imagery and, ultimately, estimating ground deformation using Interferometric Synthetic Aperture Radar (InSAR).

Reliable InSAR analysis depends on more than producing an interferogram. It requires selecting compatible acquisitions, checking spatial and acquisition geometry, handling complex SAR data, and recording which inputs and processing steps produced each result. This repository focuses on those foundations before extending the workflow to validated deformation products.

> **Status — active development.** The repository contains Sentinel-1 acquisition and metadata workflows, spatial and geometry-validation code, baseline calculations, and PostgreSQL/PostGIS models and services for tracking inputs and processing runs. A validated end-to-end chain for coregistration, interferogram generation, phase unwrapping, and line-of-sight displacement is **not yet implemented**. Cloud infrastructure and machine-learning components are future goals.

## What the project currently covers

| Component | Current scope |
| --- | --- |
| **Sentinel-1 discovery** | Query and organize Sentinel-1 bursts and associated SLC products using Copernicus catalogue metadata. |
| **Acquisition validation** | Check area-of-interest coverage, burst consistency, and acquisition metadata before processing. |
| **SAR data inspection** | Explore SAFE annotation metadata and complex SLC measurement data. |
| **Orbital and InSAR geometry** | Extract orbit state vectors, interpolate satellite states, solve zero-Doppler geometry, and calculate total, perpendicular, and parallel baselines for an acquisition pair. |
| **Metadata and provenance** | Use PostgreSQL/PostGIS and SQLAlchemy to catalog products, local source files, processing runs, run inputs, and per-burst baseline results. |
| **Engineering workflow** | Manage Python dependencies with Pixi, database migrations with Alembic, and quality checks with pytest, Ruff, pre-commit, and GitHub Actions. |

These capabilities are at different stages of development and validation. In particular, baseline geometry is **not** equivalent to a validated ground-displacement measurement.

## Processing flow

The current application is organized around the following stages:

```text
Area of interest (AOI)
       |
       v
Sentinel-1 catalogue discovery
       |
       v
Spatial and acquisition-metadata validation
       |
       v
SLC product / annotation acquisition
       |
       v
Orbit interpolation and zero-Doppler geometry
       |
       v
Reference–secondary baseline calculation
       |
       v
PostgreSQL/PostGIS metadata and processing-run records
```

The pipeline is designed to keep scientific computations separate from acquisition, orchestration, and persistence concerns. Full interferometric processing is a planned extension, not a capability claimed by this diagram.

## Technology

- **Python 3.12** for scientific processing and application logic.
- **Shapely, PyProj, Rasterio, SciPy, NumPy** for geospatial and numerical operations.
- **PostgreSQL + PostGIS**, **SQLAlchemy**, and **Alembic** for spatial metadata, provenance, and schema migrations.
- **Pixi** for reproducible environments and development tasks.
- **Docker Compose** for a local PostGIS database.
- **pytest, Ruff, pre-commit, GitHub Actions** for testing and code quality.

## Code organization

```text
src/insar_platform/
  app/         Application entry point
  config/      Configuration and logging
  ingestion/   Sentinel-1 catalogue and product metadata
  geometry/    Spatial validation, orbit and baseline calculations
  pipelines/   Discovery, acquisition and geometry workflows
  database/    SQLAlchemy models and persistence services
alembic/      Database migrations
tests/        Unit and scientific validation tests
docs/         Technical notes and scientific investigation records
```

## Getting started

### Requirements

- [Pixi](https://pixi.sh/)
- Docker with Docker Compose
- Credentials for the Copernicus Data Space Ecosystem where required
- Local environment variables and configuration appropriate for your system

The pipeline uses real Sentinel-1 data and external services. Running the full application may require access credentials, configured acquisition parameters, sufficient storage, and a running PostgreSQL/PostGIS instance.

### 1. Set up the Python environment

```bash
pixi install -e dev
```

### 2. Configure PostgreSQL/PostGIS

Set `POSTGRES_PASSWORD` in your local `.env` file or environment, then start the database:

```bash
docker compose up -d
```

Set `DATABASE_URL` to the corresponding PostgreSQL connection string. **Do not commit passwords or access tokens.**

If the database schema has not yet been applied:

```bash
pixi run -e dev alembic upgrade head
```

The repository already contains Alembic configuration; **do not run `alembic init` when setting up an existing clone**.

### 3. Run the application

```bash
pixi run -e dev insar
```

Review `src/insar_platform/config/config.yaml` before running the pipeline: it includes the area of interest, acquisition dates, SAR selection parameters, and local data paths.

### 4. Run development checks

```bash
pixi run -e dev test
pixi run -e dev lint
pixi run -e dev format-check
```

Scientific validation tests are configured separately:

```bash
pixi run -e dev validation
```

> **Note:** These are repository-defined commands, not a claim that they have been independently executed or verified on a clean machine. Check the latest GitHub Actions run for the current CI status.

## Database and provenance

The database currently models five related entities:

| Entity | Purpose |
| --- | --- |
| `SARProduct` | Identify and catalog Sentinel-1 source acquisitions. |
| `SARProductFile` | Associate measurement and annotation files with source products. |
| `ProcessingRun` | Track pipeline executions, status, timestamps, and errors. |
| `ProcessingRunInput` | Record which SAR products were used in each run. |
| `InSARBaseline` | Store per-burst baseline components for reference and secondary acquisitions. |

Registration services support repeatable insertion patterns, although metadata conflict handling and end-to-end provenance consistency still require further validation.

## Scientific validation and limitations

The project treats scientific correctness as a separate requirement from software execution. Areas under investigation include:

- Acquisition compatibility, burst identity, and common AOI coverage.
- Orbit state-vector interpolation and time-reference consistency.
- Zero-Doppler geometry and baseline decomposition.
- Complex SLC measurements and burst-level validity information.
- Traceability of inputs, parameters, and processing results.

See the documents under `docs/` for investigation notes and validation evidence. Some documented numerical findings are reported from prior investigations and have **not** been independently reproduced in this README.

**Not yet implemented or validated:** a complete SLC coregistration-to-displacement chain, production-scale orchestration, deformation time-series generation, anomaly-detection models, and a cloud-native deployment.

## Roadmap

1. Consolidate tests and validation for acquisition selection, geometry, and database provenance.
2. Integrate established InSAR scientific tools for coregistration, interferograms, coherence, and phase unwrapping.
3. Validate line-of-sight displacement estimates against reference datasets and documented scientific checks.
4. Extend product lineage, reproducibility metadata, and automated workflow recovery.
5. Evaluate cloud orchestration and machine learning only after the scientific pipeline is validated.

# Sentinel-1 Pipeline — Processing and Validation Summary

**Scope:** Review of the supplied Python modules. **Status legend:** Implemented = enforced by an exception or explicit validation; Partial = checked only in part or merely logged; Missing = no corresponding check found in the reviewed code; Planned = stage not implemented in `application.py`.

## Processing flow

```text
sentinel1_app()                                      application.py
    |
    +-- Load AOI and initialize Sentinel-1 ingestion
    |
    +-- catalog_discovery_pipeline()                  catalog.py
    |       Search and group Sentinel-1 bursts
    |       Summarize metadata and identify duplicates
    |       Validate AOI footprint geometry and coverage
    |       Compare burst identities across products
    |       Validate acquisition metadata
    |       Register SAR products; inspect orbit metadata
    |
    +-- acquisition_pipeline()                        acquisition.py
    |       Select two earliest acquisitions
    |       Retrieve product nodes, measurement and annotation
    |       Match catalog burst IDs to annotation bursts
    |       Inspect burst validity and sampling metadata
    |       Validate SLC TIFF against annotation mask
    |       Inspect adjacent burst boundary
    |       Register measurement and annotation files
    |
    +-- insar_processing_pipeline()                   insar.py
    |       Require exactly two acquisitions
    |       Extract annotation orbit vectors
    |       Normalize acquisition timestamps and check orbit coverage
    |       Interpolate satellite position and velocity
    |       Extract geolocation grid and select reference ground point
    |       Solve zero-Doppler time for each acquisition
    |       Map solution to selected burst line; log valid samples
    |       Calculate total, parallel and perpendicular baseline
    |       Check baseline decomposition
    |       Register baseline in database
    |
    +-- Coregistration                              PLANNED
    |
    +-- Interferogram generation                     PLANNED
```

## Catalog discovery — validations

| Validation | Location | Status | Short description |
|---|---|---|---|
| Bursts found | `catalog.py:39–40` | Implemented | Reject empty search results. |
| AOI geometry | `spatial_validation.py:41–54, 124–135` | Implemented | Require valid, nonempty geometry and positive area. |
| Product footprints | `spatial_validation.py:59–101` | Implemented | Validate burst footprints and compute acquisition coverage. |
| Common AOI coverage | `spatial_validation.py:104–120` | Partial | Compute coverage and verify consistency; no minimum-coverage gate. |
| Burst identity | `metadata_validation.py:49–90`; `catalog.py:126–148` | Partial | Detect missing, unexpected or duplicate burst IDs; inconsistencies are logged, not rejected. |
| Acquisition metadata | `metadata_validation.py:92–127`; `catalog.py:150–159` | Implemented | Reject mismatches in configured platform, orbit, swath and polarization. |
| Duplicate burst-ID report | `catalog.py:64–67` | Partial | Log a warning if duplicates are found. |
| Orbit metadata inspection | `metadata_validation.py:129–149` | Partial | Inspect/log orbit fields; not an orbit-quality validation. |

## Acquisition — validations

| Validation | Location | Status | Short description |
|---|---|---|---|
| Selected acquisitions | `acquisition.py:49–56` | Partial | Reject empty selection; does not explicitly assert exactly two here (InSAR does). |
| IW3 VV measurement | `acquisition.py:135–146` | Implemented | Require exactly one matching measurement node. |
| IW3 VV annotation | `acquisition.py:187–199` | Implemented | Require exactly one matching annotation node. |
| Catalog-to-annotation mapping | `metadata_validation.py:152–183`; `acquisition.py:228–252` | Implemented | Reject duplicate timestamps/IDs, missing matches and invalid selected index. |
| Valid-line count | `acquisition.py:264–288` | Partial | Count and log valid azimuth lines; no minimum fraction enforced here. |
| SLC annotation mask vs TIFF | `acquisition.py:308–325` | Implemented (delegated) | Invoke `SARMeasurementValidator.compare_mask_with_tiff`; detailed thresholds reside in that validator (not supplied). |
| Adjacent burst boundary | `acquisition.py:328–351` | Implemented (delegated) | Require multiple bursts and invoke boundary inspection; detailed acceptance behavior resides in the validator (not supplied). |
| Sampling metadata | `acquisition.py:224–225, 302–305` | Partial | Extract and log; no explicit positive/finite interval check here. |
| Existing measurement download | `acquisition.py:164–179` | Partial | Reuse a nonempty local file; file integrity is assessed separately by downstream checks. |

## InSAR processing — validations

| Validation | Location | Status | Short description |
|---|---|---|---|
| Pair size | `insar.py:35–36` | Implemented | Require exactly two acquisitions. |
| Orbit vectors exist | `calculate_orbit.py:17–58` | Implemented | Require XML file, orbit timestamps and numeric position/velocity fields. |
| Orbit timestamp order | `calculate_orbit.py:60–86` | Implemented | Require strictly increasing timestamps when finding bracketing vectors. |
| Acquisition timestamp | `insar.py:59–70` | Implemented | Require timezone-aware acquisition time. |
| Orbit coverage at acquisition time | `insar.py:72–87` | Implemented | Require acquisition timestamp inside orbit-vector time span. |
| Orbit coverage for full selected burst | `insar.py:72–87` | Implemented | Does not establish coverage of selected burst start/end. |
| Orbit interpolation bounds | `calculate_orbit.py:88–140` | Implemented | Require positive vector interval and interpolation fraction within [0, 1]. |
| Geolocation grid exists | `insar.py:101–105` | Implemented | Reject empty grid. |
| Reference ground point in selected burst | `insar.py:107–115`; `calculate_orbit.py:276+` | Implemented | Central grid point is not explicitly restricted to selected burst/valid range. |
| Zero-Doppler bracket | `acquisition_geometry.py:97–115` | Implemented | Require exactly one sign-change bracket. |
| Zero-Doppler numerical convergence | `acquisition_geometry.py:141–174` | Partial | Calculate/log normalized residual; no acceptance tolerance or finite-value gate. |
| Selected burst mapping | `insar.py:134–142` | Implemented | Require burst ID in mapping; no explicit bounds check on mapped index here (acquisition already checks it). |
| Burst time timezone | `insar.py:147–155` | Implemented | Reject timezone-naive burst start. |
| Azimuth sampling interval | `insar.py:158–162` | Implemented | Used as divisor without explicit positive/finite validation. |
| Calculated azimuth line | `insar.py:162–168` | Implemented | Require line index within selected burst's validity array. |
| Selected line's valid range | `insar.py:170–180` | Partial | Log first/last samples without asserting `0 <= first <= last < width`. |
| Baseline input vectors | `baseline_geometry.py:46–73` | Implemented | Require finite 3D vectors and nonzero reference LOS length. |
| Baseline decomposition | `insar.py:197–203` | Implemented | Check `total² ≈ parallel² + perpendicular²`. |
| Baseline acceptance threshold | `insar.py:191–215` | Implemented |Configurable perpendicular/temporal baseline limit. |
| Database run ID | `insar.py:217–220` | Implemented | Require run ID before registration. |
| Precise orbit provenance | `calculate_orbit.py:17–58` | Implemented | External POEORB selected, satellite and temporal coverage checked, orbit source and file path logged, SHA-256 calculated. |

## License

**Proprietary — All Rights Reserved.** The source code is publicly viewable, but no general permission is granted to use, modify, or redistribute it. See [LICENSE](LICENSE).
