ADR-003-study-metadata.md

# InSAR metadata

## Context
After select the product IW SLC is necessary to know the metadata.

## Problem
Can we measure and characterize land subsidence in the Alto Guadalentín aquifer using Sentinel-1 InSAR and reproduce the spatial deformation pattern documented by previous studies?

## Decision

| Property | Value | Why we care |
|---|---|---|
| Platform | Sentinel-1D | Acquisition source |
| Mode | IW | Correct land acquisition mode |
| Product | SLC | Preserves complex phase |
| Processing level | Level-1 | Correct |
| Polarization | VV + VH | VV available |
| Orbit direction | Ascending | Must remain consistent within an interferometric stack |
| Relative orbit | **103** | Very important for pairing |
| Absolute orbit | 4694 | Identifies this particular orbit |
| Sensing time | 2026-09-22 18:01 UTC | Temporal baseline |
| Swaths | IW1, IW2, IW3 | TOPS sub-swaths |
| Size | ~7.7 GB | Important engineering constraint |
| Slice | 3/22 | Product is one slice of the datatake |

Found 463 Sentinel-1 bursts                                                                                                                 
INFO     Platforms: Counter({'A': 276, 'C': 187})
INFO     Tracks: Counter({('ASCENDING', 103, 'IW1'): 159, ('ASCENDING', 1, 'IW3'): 154, ('DESCENDING', 8, 'IW2'): 150})

INFO     Group: ('A', 'ASCENDING', 1, 'IW3'), Unique SLC products: 31
INFO     Group: ('A', 'ASCENDING', 103, 'IW1'), Unique SLC products: 30
INFO     Group: ('A', 'DESCENDING', 8, 'IW2'), Unique SLC products: 31
INFO     Group: ('C', 'ASCENDING', 1, 'IW3'), Unique SLC products: 21
INFO     Group: ('C', 'ASCENDING', 103, 'IW1'), Unique SLC products: 46
INFO     Group: ('C', 'DESCENDING', 8, 'IW2'), Unique SLC products: 19