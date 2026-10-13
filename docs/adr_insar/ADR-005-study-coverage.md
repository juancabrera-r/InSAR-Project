ADR-005-study-coverage.md

# InSAR coverage

## Context
We need to determine whether the available SAR imagery provides complete coverage of AOI.

SAR Burst: a segment of rada data acquired during a satellite observation.

## Problem
The available SAR bursts do not fully cover the original AOI, resulting in incomplete spatial coverage.

## Solution
We have adjusted the AOI boundaries to match the spatial coverage of the available SAR bursts.

This ensures that each selected burst provides 100% coverage of the adjusted AOI, allowing us to maintain consistent spatial coverage across acquisitions.

## Script
export_aoi.py