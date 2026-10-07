ADR-002-study-area-and-data.md

# InSAR fundamentals

## Context
We need select an area and data for that area.

## Problem
Can we measure and characterize land subsidence in the Alto Guadalentín aquifer using Sentinel-1 InSAR and reproduce the spatial deformation pattern documented by previous studies?

## Decision
Sentinel-1 Level-1 IW SLC was selected because interferometric processing requires the complex SAR signal and its phase information. GRD products do not retain the phase required to generate conventional interferograms.

The area selected is Alto Guadalentín Basin / Aquifer, Murcia, Spain where is suffering from land subsidence due to groundwater extraction.

Sentinel-1 provides C-band SAR with repeated acquisition and long historical archive.
Copernicus provides Sentinel-1 SLC coverage.

| Criterion | Alto Guadalentín |
|---|---|
| Known deformation | Yes |
| Magnitude | Very large, locally >10 cm/year |
| Spatial extent | Basin scale |
| Physical mechanism | Groundwater-related sediment compaction |
| Previous InSAR evidence | Yes |
| Sentinel-1 evidence | Yes |
| Independent validation | GNSS/GPS, geological and groundwater information |
| Time-series potential | Excellent |

| Study area:   |  Alto Guadalentín |
| --- | --- |
| Mission:    |         Sentinel-1 |
| Product level:   |    Level-1 |
| Product type:   |     SLC |
| Acquisition mode:   | IW |