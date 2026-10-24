# ADR-012: Study different geometry alignment

## Context

The Sentinel-1 InSAR pipeline has completed acquisition, burst identification, annotation validation, precise-orbit selection (POEORB), and baseline geometry. The next step is **coregistration**: mapping the secondary complex SLC onto the reference acquisition's radar grid while preserving phase.

The current implementation is Python-based and processes Sentinel-1 IW TOPS bursts. TOPS coregistration requires accurate range/azimuth alignment, handling burst boundaries and valid-sample masks, and fine azimuth refinement (potentially Enhanced Spectral Diversity, ESD) for interferometric accuracy.

We need to select an approach for geometric alignment and complex SLC resampling without replacing the existing acquisition, provenance, and validation components.

## Options considered

### Option 1 - Custom implementation with NumPy / SciPy

**Description:** Implement radar-to-ground/ground-to-radar geometry, reference-to-secondary offset estimation, complex resampling, and validation directly in the existing Python codebase.

**Advantages**
- Full control of algorithms, interfaces, and validation criteria.
- Integrates naturally with existing metadata, orbit, and database models.
- Minimal additional processing-framework dependencies.
- Easy to instrument and test individual numerical steps.

**Disadvantages**
- Highest development and scientific-validation effort.
- Greater risk of subtle timing, interpolation, Doppler, and phase errors.
- May require compiled/GPU acceleration for large SLC datasets.
- TOPS-specific fine alignment and ESD must be developed and validated separately.

### Option 2 - ISCE3 library

**Description:** Integrate selected ISCE3 C++/Python algorithms for radar geometry, offset computation, and resampling, while keeping pipeline orchestration in Python.

**Advantages**
- Reuses established SAR geometry and numerical processing components.
- Optimized implementations can improve performance.
- Retains modularity: existing CDSE ingestion, orbit provenance, and database code remain in place.
- Reduces the amount of low-level geometry code to maintain.

**Disadvantages**
- Installation, packaging, and platform compatibility are more complex, especially on native Windows.
- Requires adapters between Sentinel-1 SAFE metadata and ISCE3 data structures.
- Not a plug-and-play end-to-end Sentinel-1 TOPS coregistration pipeline; TOPS-specific steps still require integration.
- Adds a substantial external dependency and learning curve.

### Option 3 - ESA SNAP / Sentinel-1 TOPS workflow

**Description:** Use SNAP's Sentinel-1 TOPS processing operators (for example, Back-Geocoding and Enhanced Spectral Diversity) through SNAP Graph Processing Tool (GPT), orchestrated by the Python pipeline.

**Advantages**
- Established Sentinel-1 TOPS-specific processing workflow.
- Supports DEM-assisted geometric coregistration and ESD refinement.
- Faster route to an end-to-end reference result.
- Useful as an independent benchmark for a custom or ISCE3 implementation.

**Disadvantages**
- External Java/GPT runtime with significant memory and configuration requirements.
- Less fine-grained control over internal algorithms and intermediate data.
- Requires data-format conversion or SAFE-compatible inputs/outputs and subprocess orchestration.
- Harder to integrate with the current burst-level Python architecture and provenance model.

## Comparison

| Criterion | Custom Python | ISCE3 | SNAP / GPT |
|---|---|---|---|
| Implementation effort | High | Medium–high | Low–medium for standard workflow |
| Algorithm control | High | Medium–high | Medium–low |
| Sentinel-1 TOPS-specific workflow | Must build | Must integrate | Available |
| Integration with current Python architecture | Direct | Via bindings/adapters | Via external process |
| Deployment complexity | Low–medium | Medium–high | Medium–high |
| Independent reference for validation | No | Potentially | Yes |

## Decision

**Status: Proposed - no implementation selected yet.**

Evaluate the three approaches using the same Sentinel-1A IW3/VV acquisition pair (2025-01-04 reference, 2025-01-16 secondary) and burst 219, with adjacent bursts 218 and 220 available for TOPS overlap analysis. Compare geometric offset fields, resampled SLC quality, fine-alignment residuals, execution time, and reproducibility.

**Suggested evaluation path:** use SNAP as a reference baseline, prototype ISCE3 integration, and adopt custom NumPy/SciPy only where the required algorithms are sufficiently bounded and can be independently validated.

## Acceptance criteria

- Both acquisitions are mapped to a common reference radar grid using the correct POEORB and timing metadata.
- Complex SLC phase is preserved during interpolation.
- Valid-sample masks and burst boundaries are respected.
- Range and azimuth residuals are measured against explicit tolerances.
- TOPS-specific azimuth refinement is evaluated using overlapping bursts where applicable.
- The chosen approach records software version, input provenance, configuration, and validation metrics.
