# Cloud-Native InSAR Processing & ML Platform

A learning-oriented, evolving platform for processing Sentinel-1 Synthetic Aperture Radar (SAR) data, managing InSAR metadata and provenance, and eventually analyzing ground deformation with geospatial analytics and machine learning.

> **Project status:** Early development. Sentinel-1 data retrieval, acquisition-geometry analysis, and complex SLC inspection have been explored. PostgreSQL/PostGIS schema development and validation are in progress. A validated end-to-end InSAR displacement pipeline and the cloud/ML components are **not yet implemented**.

## Project objectives

- Retrieve Sentinel-1 Single Look Complex (SLC) products and relevant metadata from the Copernicus Data Space Ecosystem (CDSE).
- Understand and validate acquisition geometry, orbit state vectors, zero-Doppler geometry, and interferometric baselines.
- Build reproducible InSAR processing workflows using established scientific software rather than reimplementing complex algorithms.
- Store SAR product metadata, source-file references, processing runs, run inputs, and per-burst InSAR baselines in PostgreSQL/PostGIS; expand the schema for derived scientific products later.
- Progress toward displacement time series, geospatial analytics, and defensible deformation anomaly detection.
- Eventually deploy a cost-conscious cloud-native platform with AWS, orchestration, infrastructure as code, and MLOps.

## Current progress

| Area | Status | Scope |
| --- | --- | --- |
| Sentinel-1 data access | Explored | CDSE authentication, SAFE annotation XML, IW3 VV measurement TIFF |
| Acquisition geometry | Explored | Orbit vectors, Hermite interpolation, zero-Doppler solution, baseline decomposition |
| Complex SLC inspection | Explored | Raster metadata, complex samples, amplitude visualization, burst-boundary observations |
| Metadata database | In progress | PostgreSQL/PostGIS models and registration services; migration and integration validation in progress |
| End-to-end InSAR processing | Planned | Coregistration, interferograms, coherence, phase unwrapping, LOS displacement |
| Cloud data platform | Planned | AWS storage and compute, Airflow, analytical datasets |
| Machine learning / MLOps | Planned | Deformation anomaly detection, MLflow |

The explored scientific steps are not, by themselves, evidence of validated interferometric displacement estimates.

## Architecture (target)

```text
Sentinel-1 SLC / CDSE
        |
        v
Ingestion + metadata validation
        |
        +----> PostgreSQL/PostGIS (metadata and provenance)
        |
        v
InSAR processing (established scientific tools)
        |
        v
Interferograms / coherence / displacement products
        |
        v
Deformation time series + analytical datasets
        |
        v
Anomaly detection + MLflow (future)
```

**Planned cloud infrastructure:** S3 (Bronze/Silver/Gold data zones), cost-controlled compute, IAM, monitoring, Airflow orchestration, and Terraform-managed infrastructure. These are future capabilities, not prerequisites for local development.

## Repository organization

The codebase is being consolidated into a single Python package. The following is the **intended structure**; some paths may not exist yet.

```text
src/
  insar_platform/
    app/          # Application entry points
    config/       # Configuration and logging
    ingestion/    # CDSE, SAFE annotations, acquisition metadata
    geometry/     # Orbit, zero-Doppler and baseline calculations
    processing/   # SAR/InSAR processing integrations
    pipelines/    # Workflow coordination
    database/     # Database models, sessions, repositories
    api/          # Future API
scripts/          # Exploration and operational scripts
tests/            # Unit, integration, and scientific validation
alembic/          # Database migration revisions (after initialization)
```

`pipelines/` coordinates reusable functions from `ingestion/`, `geometry/`, and `processing/`. Large SLC and raster products should be stored as files or objects; the database catalogs their metadata, locations, and lineage.

## Prerequisites

- [Pixi](https://pixi.sh/) for project environments and tasks.
- [Docker](https://www.docker.com/) with Docker Compose for local PostgreSQL/PostGIS.
- A project configuration appropriate for your local environment. Do not commit credentials, tokens, or database passwords.

The commands below assume the repository already defines the `dev` Pixi environment, the `insar` task, a Docker Compose service/container named `insar_postgres`, and an Alembic configuration. Confirm these names in your local configuration before running them.

## Local development with Pixi

Install or synchronize dependencies using the repository's Pixi configuration:

```bash
pixi install -e dev
```

Enter the development environment:

```bash
pixi shell -e dev
```

Or run the configured application task directly:

```bash
pixi run -e dev insar
```

> `pixi develop` is not the standard command for entering a Pixi environment. Use `pixi shell -e dev` if your Pixi installation supports it, or run tasks with `pixi run -e dev ...`.

## Start PostgreSQL + PostGIS

Start the local services from the directory containing `compose.yaml` or `docker-compose.yml`:

```bash
docker compose up -d
```

Connect to the PostgreSQL container (adjust the container name, user, and database if your Compose configuration differs):

```bash
docker exec -it insar_postgres psql -U insar -d insar
```

Check that PostGIS is available:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Version();
```

For a new database, extension creation may be handled by an initialization script or an Alembic migration. Choose one documented approach and keep it reproducible.

To stop the services without removing database volumes:

```bash
docker compose down
```

**Warning:** `docker compose down -v` also removes Compose-managed volumes and may delete your local database data.

## Database migrations with Alembic

Alembic manages versioned schema changes. Review generated migrations before applying them: autogeneration may miss or misinterpret certain database changes, particularly spatial types, constraints, or extension-specific operations.

### One-time initialization

Run this **only if Alembic has not already been initialized** in the repository:

```bash
pixi run -e dev alembic init alembic
```

Configure `alembic.ini` and `alembic/env.py` to use the correct database connection and SQLAlchemy model metadata. Keep credentials outside version control.

### Generate a migration after changing models

```bash
pixi run -e dev alembic revision --autogenerate -m "initial insar schema"
```

Inspect the generated revision under `alembic/versions/` before applying it. Use a new descriptive message for subsequent changes.

### Inspect migration state

```bash
# Revision currently applied to PostgreSQL
pixi run -e dev alembic current

# Latest revision(s) in the repository
pixi run -e dev alembic heads

# Migration history
pixi run -e dev alembic history
```

### Apply migrations

```bash
pixi run -e dev alembic upgrade head
```

### Roll back one revision

```bash
pixi run -e dev alembic downgrade -1
```

**Caution:** A downgrade is not necessarily lossless. Review its operations and back up important data before rolling back a populated database.

## Database schema and provenance (current implementation)

The current SQLAlchemy models define **five tables**. They track Sentinel-1 source products, locally registered SAFE components, pipeline executions, the products used by those executions, and calculated InSAR baselines. These are **not yet** a complete catalog of generated interferograms or displacement products.

| Table / model | Purpose | Important fields and constraints |
| --- | --- | --- |
| `sar_product` / `SARProduct` | Catalog a Sentinel-1 SAR product | UUID primary key; unique `product_name`; `satellite`, `acquisition_time`, optional `relative_orbit`, optional PostGIS `MULTIPOLYGON` footprint with SRID 4326 |
| `sar_product_file` / `SARProductFile` | Register local measurement TIFFs and annotation XML files | References `sar_product`; `file_type` (`measurement` or `annotation`), `file_path`, optional file size and JSONB metadata; unique `(product_id, file_type, file_path)`; optional `download_run_id` |
| `processing_runs` / `ProcessingRun` | Record pipeline executions and their outcomes | UUID, `pipeline_name`, creation/start/end timestamps, `status` (`pending`, `running`, `completed`, `failed`, `cancelled`), optional error message |
| `processing_run_input` / `ProcessingRunInput` | Associate input SAR products with processing runs | Composite primary key `(run_id, product_id)`; foreign keys to `processing_runs` and `sar_product` |
| `insar_baseline` / `InSARBaseline` | Persist per-burst baseline calculations for a reference/secondary SAR product pair | `run_id`, both product IDs, `catalogue_burst_id`, total/perpendicular/parallel baseline components in meters; distinct-product check and uniqueness per run/pair/burst |

### Persistence services

The existing database services provide these operations:

- `register_sar_products`: insert previously unseen SAR products by `product_name`.
- `register_sar_product_files`: validate that local files exist and are nonempty, then register them without duplicating the same product/type/path combination.
- `register_processing_run_inputs`: associate already registered SAR products with a run without duplicate links.
- `register_insar_baseline`: resolve product names to IDs, validate finite baseline values and the baseline decomposition, then register a per-burst result.
- `run_tracked_pipeline`: create a run, execute a pipeline, optionally register its inputs and persist its results, and mark it completed or failed.

The registration functions use PostgreSQL conflict handling for repeat inserts. **Important limitation:** a conflicting insert generally preserves the existing record; it does not verify that all newly supplied metadata matches the existing row.

### Database validation checklist

- Confirm that PostGIS is enabled and that footprints are valid `MULTIPOLYGON` geometries in EPSG:4326.
- Verify uniqueness of SAR product names, file records, run-input associations and baseline records.
- Verify foreign-key behavior, including `CASCADE`, `RESTRICT` and `SET NULL` where configured.
- Test repeated registration: duplicates should not be inserted unintentionally, and conflicting metadata should be handled explicitly.
- Check that baseline values are finite and satisfy the expected decomposition within a justified numerical tolerance.
- Check scientific provenance: the baseline's reference/secondary products should be consistent with the products linked to its processing run. **This consistency is not enforced by the current foreign keys alone.**
- Test failure handling and recovery, including what happens if result persistence succeeds but final run-status persistence fails.
- Run migrations and integration tests against a clean PostgreSQL/PostGIS database.

### Planned schema extensions

As the scientific workflow develops, consider recording software versions, processing parameters, input checksums, orbit/DEM references, baseline calculation conventions, and derived interferogram/coherence/displacement products. These are **future requirements**, not fields currently present in the supplied models.

## Development roadmap

1. **InSAR fundamentals:** inspect Sentinel-1 SLC data and validate scientific calculations.
2. **Reproducible local processing:** integrate established InSAR tools with configuration, logging, and tests.
3. **Database and provenance:** model acquisitions, pairs, runs, and products in PostgreSQL/PostGIS. **Current engineering focus.**
4. **AWS foundation:** introduce S3, IAM, and minimal cloud compute with cost controls.
5. **Workflow orchestration:** use Airflow for retries, scheduling, and recovery.
6. **Analytical data platform:** create validated displacement time-series datasets and Parquet outputs.
7. **Scalable analytics:** introduce PySpark only where data size and benchmarking justify it.
8. **ML/MLOps:** define deformation anomaly detection and track experiments with MLflow.
9. **Production engineering:** CI/CD, monitoring, security, Terraform, and operational documentation.
10. **Optional extensions:** evaluate advanced infrastructure or models only when required.

## Scientific and engineering principles

- **Scientific validity first:** verify acquisition geometry, temporal/perpendicular baselines, phase products, geocoding, and displacement units before automating results.
- **Reproducibility:** record input identifiers, parameters, software versions, orbit/DEM dependencies, and output checksums.
- **Idempotency and recovery:** support safe retries and retain failed and repeated processing attempts.
- **Data quality:** check CRS, extent, resolution, transforms, masks/NoData, spatial alignment, and acquisition timing when applicable.
- **Cost awareness:** prefer small local experiments and avoid unnecessary always-on cloud infrastructure.

## License

Proprietary — All Rights Reserved. Permission is required before using, modifying or distributing this software. See LICENSE.

