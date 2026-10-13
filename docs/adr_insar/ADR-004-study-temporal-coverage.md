ADR-004-study-temporal-coverage.md

# InSAR temporal converage

## Context

After analyzing the SAR product metadata, we need to evaluate the temporal coverage of the available acquisitions.

For this study, we have selected the period from 2025 to 2026 to assess the availability, temporal distribution, and consistency of SAR observations.

SLC (Single Look Complex): A SAR product that preserves amplitude and phase information, required for conventional interferometric processing.

## Problem

The metadata analysis identified 463 burst records from two Sentinel-1 platforms:

- Sentinel-1A: 276 burst records.
- Sentinel-1C: 187 burst records.

The records are distributed across three orbital configurations:

| Orbit direction | Relative orbit | Subswath | Burst records |
|---|---:|---|---:|
| Ascending | 103 | IW1 | 159 |
| Ascending | 1 | IW3 | 154 |
| Descending | 8 | IW2 | 150 |

The reported number of unique SLC products per group is:

| Platform | Orbit direction | Relative orbit | Subswath | Unique SLC products |
|---|---|---:|---|---:|
| Sentinel-1A | Ascending | 1 | IW3 | 31 |
| Sentinel-1A | Ascending | 103 | IW1 | 30 |
| Sentinel-1A | Descending | 8 | IW2 | 31 |
| Sentinel-1C | Ascending | 1 | IW3 | 21 |
| Sentinel-1C | Ascending | 103 | IW1 | 46 |
| Sentinel-1C | Descending | 8 | IW2 | 19 |

The supplied acquisition-date sequence extends from January 4, 2025, to December 30, 2025. It contains 31 dates, with a consistent 12-day interval between consecutive observations (360 days from first to last).

The sequence does not identify its platform, relative orbit, or subswath. Therefore, its temporal regularity cannot be assumed to apply to all groups.

## Decision

We will assess temporal coverage separately for each orbital configuration before selecting the acquisition series for InSAR processing. The assessment will prioritize:

- Consistent acquisition geometry (orbit direction, relative orbit, subswath, and burst coverage).
- Regular temporal sampling with minimal gaps.
- Sufficient acquisition dates for interferometric time-series analysis.
- Spatial coverage of the adjusted AOI, as addressed in [ADR-005](ADR-005-study-coverage.md).

The 31-date sequence from 2025 is an initial candidate, not a finalized selection. Before finalizing the dataset, we will identify its associated platform and orbital configuration, verify acquisition dates for each selected burst, and assess coverage during 2026.

## Consequences

- Temporal gaps and acquisition consistency can be evaluated without mixing distinct orbital configurations.
- The final time series may contain fewer observations than the full metadata inventory.
- Complete geometric coverage does not by itself guarantee interferometric coherence or valid measurements throughout the AOI.
