# ADR-006: InSAR Acquisition Consistency

## Context

After evaluating temporal coverage (ADR-004) and adjusting the Area of Interest (AOI) to the available SAR coverage (ADR-005), we need to verify that the selected acquisitions are spatially and structurally consistent for InSAR processing.

The consistency analysis evaluates product identifiers, AOI coverage, burst counts, burst numbers, and subswath information.

**When is a SAR burst considered consistent?**
In InSAR, burst consistency means that acquisitions from different dates observe the same ground area using compatible acquisition geometry and burst alignment, allowing interferometric processing.

The level of consistency are:

| Requirement | Evidence | Status |
|---|---|---|
| Platform | Sentinel-1A and Sentinel-1C identified | Verify selected stack |
| Acquisition mode | IW | Confirmed at dataset level |
| Product type | SLC | Confirmed at dataset level |
| Polarization | VV + VH in example metadata | Verify all 31 |
| Orbit direction | Ascending for candidate group | Verify selected stack |
| Relative orbit | 1 for candidate IW3 group | Verify selected stack |
| Subswath | IW3 across 31 acquisitions | Confirmed |
| Burst numbers | 218, 219, 220 across 31 acquisitions | Confirmed |
| AOI coverage | 100% across 31 acquisitions | Confirmed by logs |
| Temporal interval | 12 days in supplied 2025 sequence | Confirmed for that sequence |
| Co-registration | No results supplied | Pending |

## Problem

Acquisitions may have different spatial footprints or burst configurations. These differences can prevent consistent coverage of the AOI across an InSAR time series.

We therefore need to confirm that the selected acquisitions cover the same AOI and contain the same burst-number set.

## Findings

### Dataset identifier checks

The initial metadata check reported:

| Metric | Result |
|---|---:|
| Burst records | 463 |
| Unique burst IDs | 463 |
| Duplicate burst IDs | 0 |

The log reports no duplicate burst IDs. This check covers the initial dataset and should not be confused with the 31-acquisition subset evaluated below.

### Selected acquisition consistency

| Metric | Result |
|---|---|
| Selected acquisitions | 31 |
| Bursts per acquisition | 3 |
| Subswath | IW3 |
| Burst numbers | 218, 219, 220 |
| AOI coverage per acquisition | 100.00% (reported) |
| Common AOI coverage across acquisitions | 100.00% (reported) |
| Burst-number consistency | True |

Every listed acquisition contains three bursts. The order of bursts varies in the metadata, but the set of burst numbers remains **{218, 219, 220}** across all 31 acquisitions.

The validation logs report **100.00% AOI coverage** for every acquisition and **100.00% common AOI coverage** across the selected 31 acquisitions.

The reported AOI polygon is:

```json
{
  "type": "Polygon",
  "coordinates": [[
    [-1.280923, 37.651325],
    [-1.801921, 37.727386],
    [-2.294356, 37.797148],
    [-2.336068, 37.604716],
    [-1.844013, 37.539117],
    [-1.323416, 37.467227],
    [-1.280923, 37.651325]
  ]]
}
```

### Burst identity interpretation

The `burst_id` values are consistent across acquisitions, while the `absolute_burst_id` values differ. Changing absolute IDs alone does not demonstrate inconsistent burst alignment. The logs establish matching burst-number sets and subswath, but do not independently establish full interferometric compatibility.

## Decision

We will retain the selected 31-acquisition IW3 sequence with burst numbers **218, 219, and 220** as a candidate dataset for InSAR processing.

The selection is supported by the reported full AOI coverage, common coverage across acquisitions, and consistent burst-number sets.

Before interferometric processing, we will additionally verify the relative orbit, acquisition mode, polarization, burst alignment, orbital metadata, and any processing-specific compatibility requirements. These properties are not established by the supplied consistency logs.

## Consequences

- The selected acquisitions have a consistent three-burst configuration according to the metadata checks.
- The adjusted AOI is reported to be covered by all 31 acquisitions, avoiding coverage gaps at the footprint level.
- Restricting processing to the adjusted AOI may exclude areas from the original study region, as documented in ADR-005.
- Geometric footprint coverage does not guarantee valid SAR pixels or sufficient interferometric coherence throughout the AOI.

## Related decisions

- **ADR-004:** InSAR Temporal Coverage
- **ADR-005:** InSAR Coverage and Consistency
