# Sentinel-1 InSAR Processing Platform

**A modular Python platform for reproducible Sentinel-1 SLC acquisition, interferometric processing, scientific validation, and processing provenance.**

> **Status — active development (October 2026).** The application includes catalogue discovery, acquisition inspection, orbital and baseline geometry, SNAP TOPS coregistration, interferogram/coherence generation, Goldstein phase filtering, SNAPHU phase unwrapping, and automated unwrapping validation. A separate DEM-based topographic phase removal stage has been integrated into the application code but has not yet been demonstrated to complete successfully in the provided run. **Validated, geocoded line-of-sight (LOS) displacement and deformation time series are not yet established.**

## Objective

Process compatible Sentinel-1 Single Look Complex (SLC) acquisitions into traceable interferometric products, with explicit validation at each stage. Successful software execution, correct phase unwrapping modulo \(2\pi\), and scientifically validated ground displacement are **different milestones**.

## Current capabilities

| Component | Implementation and validation status |
| --- | --- |
| Sentinel-1 catalogue discovery | Implemented: search, burst grouping, AOI coverage and acquisition metadata checks. |
| Acquisition and SAFE inspection | Implemented: measurement/annotation retrieval, burst mapping and input inspection. |
| Orbit and baseline geometry | Implemented: state-vector interpolation, zero-Doppler geometry and baseline decomposition. |
| Database and provenance | Implemented: PostgreSQL/PostGIS and SQLAlchemy models and processing-run tracking; completeness of lineage still needs review. |
| TOPS coregistration | Implemented through ESA SNAP GPT, including Back-Geocoding and optional ESD; quality checks require ongoing scientific assessment. |
| Interferogram and coherence | Implemented using SNAP; supports flat-Earth subtraction and configurable coherence estimation. |
| Coherence inspection | Implemented: summary statistics and histogram/report generation. |
| Topographic phase removal | Implemented as a separate SNAP `TopoPhaseRemoval` stage in the application; end-to-end execution not yet confirmed. |
| Goldstein phase filtering | Implemented using SNAP; must consume the topographically corrected interferogram. |
| SNAPHU phase unwrapping | Implemented with SNAP export/import and tiled SNAPHU execution. |
| Unwrapping validation | Implemented and successfully run on an earlier **non-topographically corrected** processing chain. |
| Terrain correction and LOS displacement | Not yet demonstrated as implemented or scientifically validated in the supplied application. |
| Deformation time series / cloud ML | Future work. |

## Processing workflow

The current application (`sentinel1_app()` in `application.py`) orchestrates the following sequence:

```text
Area of interest (AOI)
    |
    v
1. Catalogue discovery and burst/acquisition validation
    |
    v
2. Acquisition and SAFE inspection/download
    |
    v
3. Orbit and baseline geometry
    |
    v
4. SAFE dependency inspection
    |
    v
5. SNAP TOPS coregistration (Back-Geocoding / ESD)
    |
    v
6. Interferogram and coherence generation
   (flat-Earth subtraction; topographic subtraction OFF)
    |
    +--> Coherence inspection/report
    |
    v
7. DEM-based topographic phase removal (SNAP TopoPhaseRemoval)
    |
    v
8. Goldstein phase filtering
    |
    v
9. SNAPHU phase unwrapping
    |
    v
10. Automated phase-unwrapping validation
    |
    v
Future: terrain correction -> LOS displacement -> external validation
```

The numbered processing blocks above are conceptual. In the current `application.py`, AOI preparation is step 1, catalogue discovery step 2, acquisition step 3, baseline step 4, SAFE inspection step 5, coregistration step 6, interferogram step 7, coherence inspection step 8, **topographic phase removal step 9**, **Goldstein filtering step 10**, and **phase unwrapping step 11**. Automated validation is called by the unwrapping pipeline.

### Topographic phase removal

The measured interferometric phase includes geometric/topographic, deformation, atmospheric and residual-noise contributions. SNAP simulates the topographic phase using a DEM and acquisition geometry, then subtracts it to reduce elevation-dependent fringes before filtering and unwrapping:

```text
wrapped interferogram -> TopoPhaseRemoval -> corrected wrapped interferogram
```

Recommended settings based on the installed SNAP operator help:

| Parameter | Value |
| --- | --- |
| Operator | `TopoPhaseRemoval` |
| DEM | `Copernicus 30m Global DEM` |
| `orbitDegree` | `3` |
| `outputTopoPhaseBand` | `true` |
| `outputElevationBand` | `true` |
| `outputLatLonBands` | `false` |
| `tileExtensionPercent` | `100` |
| Output format | `BEAM-DIMAP` (`.dim` and `.data`) |

**Avoid double subtraction:** when using the separate `TopoPhaseRemoval` stage, keep `INTERFEROGRAM_SUBTRACT_TOPOGRAPHIC_PHASE=false` in the upstream `Interferogram` operator. The alternative is to enable topographic subtraction directly in `Interferogram` and omit the separate stage; do not combine both methods.

**Critical wiring:** the Goldstein filtering call must receive `interferogram_dim=topographic_dim`, not the original `interferogram_dim`. Otherwise the topographic correction is calculated but not used by the downstream chain. The last shared application revision still passed the original product to Goldstein; this wiring change is required before claiming the updated workflow is operational.

### Phase filtering and unwrapping

Goldstein filtering reduces phase noise while preserving interferometric fringe structure. The filtered interferogram is exported for SNAPHU, unwrapped and reimported into a SNAP BEAM-DIMAP product. Example configuration used in the project:

| SNAPHU parameter | Value |
| --- | --- |
| Statistical cost mode | `DEFO` |
| Initialization | `MST` |
| Tiles | 4 rows × 8 columns |
| Row / column overlap | 200 pixels each |
| Processors | 4 |

The unwrapping validation compares unwrapped phase against the wrapped phase modulo \(2\pi\), evaluates phase jumps, checks coherence and reports coverage. It cannot independently prove the correct absolute number of phase cycles or the accuracy of derived ground displacement.

### Processing cache and reproducibility

Pipeline stages use `run_or_load_pipeline()` with stage-specific `.pkl` caches and `run_tracked_pipeline()` for processing-run tracking. A `.pkl` cache records a Python stage result; it is **not** a substitute for the actual SNAP `.dim`/`.data` product. Skipped stages should verify both cache presence and referenced output-product integrity.

After changing topographic correction, regenerate **topographic removal → Goldstein filtering → SNAPHU unwrapping → validation**. Reuse the upstream coregistration/interferogram only when their parameters and source data remain unchanged. Stale downstream `.pkl` files or output products must not be treated as valid for the new chain.

## Scientific validation and limitations

### Previous successful unwrapping validation

The following metrics are from the **earlier interferogram without the newly added topographic phase removal**. They must not be presented as validation of the updated topographically corrected chain.

| Metric | Previous result |
| --- | ---: |
| Raster dimensions | 25,286 × 4,199 pixels |
| Valid pixels | 98,087,052 |
| Valid fraction | 92.38% |
| Circular residual mean absolute error | 0.000004 rad |
| Circular residual RMSE | 0.000007 rad |
| Fraction with circular residual > 0.1 rad | 0.0000% |
| Adjacent phase jumps > π | 0.3801% |
| Estimated tile-boundary jumps > π | 0.3772% |
| Interior jumps > π | 0.3801% |
| Mean coherence over validation mask | 0.6914 |
| Validator result | Passed |

These results support **wrapped/unwrapped modulo-\(2\pi\) consistency**, not independently validated displacement. Residual atmosphere, orbital errors, DEM errors, decorrelation, unwrapping cycle mistakes and terrain-geocoding effects still require assessment.

### Required next scientific checks

1. Confirm the new topographic stage produces a valid SNAP product with expected corrected interferometric and optional diagnostic bands.
2. Verify Goldstein filtering consumes that corrected product; rerun downstream stages and their validation.
3. Inspect elevation-correlated residual fringes and DEM coverage/voids; quantify changes relative to the original interferogram.
4. Apply appropriate terrain correction/geocoding and convert valid unwrapped phase to LOS displacement with a documented phase-sign convention.
5. Compare displacement against independent reference observations or stable-ground controls; characterize atmospheric, orbital and unwrapping uncertainty.

## Technology

- Python 3.12; NumPy, SciPy, Rasterio, Shapely and PyProj for scientific/geospatial operations.
- ESA SNAP/S1TBX Graph Processing Tool (GPT) for TOPS coregistration, interferometry, DEM correction, filtering and SNAPHU import/export.
- SNAPHU for phase unwrapping.
- PostgreSQL/PostGIS, SQLAlchemy and Alembic for metadata and provenance.
- Pixi for environments; Docker Compose for local database services.
- pytest, Ruff, pre-commit and GitHub Actions for development checks.

## Code organization

```text
src/insar_platform/
  app/           Application entry point / workflow orchestration
  config/        Configuration and logging
  ingestion/     Sentinel-1 catalogue and product metadata
  geometry/      Spatial, orbital and baseline calculations
  coregistration/ Coregistration inspection and coherence diagnostics
  pipelines/     Acquisition, coregistration, interferogram,
                 topographic_phase_removal, phase_filtering,
                 phase_unwrapping
  validations/   SNAP product and unwrapping validation
  database/      SQLAlchemy models and processing-run services
  utils/         Cache management and shared helpers
alembic/         Database migrations
tests/           Automated tests
docs/            Technical and scientific notes
```

## Getting started

### Requirements

- [Pixi](https://pixi.sh/)
- Docker and Docker Compose (for the local database)
- Copernicus Data Space credentials, when required
- ESA SNAP with Sentinel-1 Toolbox and `gpt.exe`
- SNAPHU executable
- Sufficient storage for multi-gigabyte Sentinel-1 SLC products and intermediate rasters

### 1. Install Python environment

```bash
pixi install -e dev
```

### 2. Configure PostgreSQL/PostGIS

Set `POSTGRES_PASSWORD` and `DATABASE_URL` in local environment configuration; do not commit secrets.

```bash
docker compose up -d
pixi run -e dev alembic upgrade head
```

Do not run `alembic init` in an existing repository that already contains migrations.

### 3. Configure SNAP, SNAPHU and processing parameters

Review `src/insar_platform/config/config.yaml` and any environment-specific settings. Relevant keys in the shared application include:

```yaml
SNAP_GPT: 'C:\Program Files\esa-snap\bin\gpt.exe'
SNAPHU_EXECUTABLE: 'C:\Tools\snaphu\bin\snaphu.exe'
INTERFEROGRAM_SUBTRACT_TOPOGRAPHIC_PHASE: false
TOPOGRAPHIC_PHASE_REMOVAL_OUTPUT: data/processed/topographic_phase_removal
TOPOGRAPHIC_DEM: Copernicus 30m Global DEM
TOPOGRAPHIC_ORBIT_DEGREE: 3
```

These values are **illustrative**; confirm actual executable locations and configuration layout. Stage skip flags reside under `CONFIG` in the shared application. In a first topographic-correction run, keep the upstream verified stages skipped if their caches/products are valid, and run topographic removal, phase filtering and phase unwrapping afresh.

### 4. Run the application

```bash
pixi run -e dev insar
```

### 5. Run development checks

```bash
pixi run -e dev test
pixi run -e dev lint
pixi run -e dev format-check
pixi run -e dev validation
```

These are repository-defined commands; they have not been independently verified in a clean environment for this documentation update.

## Database and provenance

The documented data model includes `SARProduct`, `SARProductFile`, `ProcessingRun`, `ProcessingRunInput` and `InSARBaseline`. Record acquisition identity, source SAFE/annotation paths, orbit source, DEM identity, SNAP/SNAPHU parameters, software versions, stage status and output-product paths where supported. End-to-end provenance completeness remains a validation task.

## Roadmap

1. Execute and validate the newly integrated topographic phase removal stage and fix downstream product wiring.
2. Confirm reliable cache invalidation and output-product integrity checks for all dependent stages.
3. Implement/verify terrain correction and LOS displacement estimation with independent scientific validation.
4. Extend product lineage, parameter auditing, recovery and automated regression tests.
5. Investigate deformation time series, scalable orchestration and ML only after the scientific pipeline is validated.

## Historical validation review

The following validation matrix was retained from an earlier source-code review. **File/line references and implementation statuses may be stale** and should be re-audited against the current repository before use as acceptance evidence.

## Catalog discovery - validations**

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

## Acquisition - validations**

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

## InSAR processing - validations**

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

**Proprietary — All Rights Reserved.** Source visibility does not grant permission to use, modify or redistribute the code. See [LICENSE](LICENSE).
