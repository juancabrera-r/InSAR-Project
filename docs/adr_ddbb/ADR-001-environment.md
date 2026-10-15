ADR-001-ddbb.md

# Study environment

## Context

We need to storage all information in a local db and later integrate in Amazon S3.

The information we neeed to store is acquisition dates, sensor characteristics, geographic coverage, processing status and storage locations.

For large data we use Amazon S3.

## Problem
The DB selected must be compatible with PostGIS

## Alternative

| PostgreSQL vs. | SQLite |
| --- | --- |
| Criterion |	PostgreSQL + PostGIS |	SQLite |
| Local development |	Good, requires a server |	Excellent |
| Concurrent processing |	Excellent |	Limited concurrent writes |
| Geospatial queries |	Native via PostGIS |	Requires an extension or application logic |
| Multiple workers |	Suitable |	Potential write contention |
| Integration with S3 |	Via application |	Via application |
| Schema migrations |	Alembic |	Alembic, with limitations |
| Production deployment |	Excellent |	Suitable for simpler workloads |
| Operational complexity |	Moderate |	Very low |

Both databases can store S3 object references. Neither requires S3 integration at the database level.

## Decision

Use PostgreSQL from the beginning, run it locally through Docker, and connect from Python using SQLAlchemy and psycopg. This avoids a future SQLite-to-PostgreSQL migration.

## Proposal database architecture

The initially design the database have five entities:

| Table |	Purpose |
| --- | --- |
| sar_products |	Catalog Sentinel-1 acquisitions, acquisition time,  |relative orbit, polarization, footprint, and source location
| insar_pairs |	Connect reference and secondary SAR products |
| processing_runs |	Track executions, algorithms, parameters,  |versions, status, and errors
| artifacts |	Register SAFE files, SLCs, interferograms, coherence  |rasters, and other outputs
| processing_run_inputs |	Track exactly which artifacts were used by  |each processing run

The database stores their metadata and references, not the large raster arrays.

## solution

**connection.py** initializes the connection infrastructure without creating tables automatically. Schema changes should be managed through Alembic.

