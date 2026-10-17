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

## License

**Proprietary — All Rights Reserved.** The source code is publicly viewable, but no general permission is granted to use, modify, or redistribute it. See [LICENSE](LICENSE).
