from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class OrbitSource(StrEnum):
    ANNOTATION = "annotation"
    POEORB = "POEORB"
    RESORB = "RESORB"


@dataclass(frozen=True)
class OrbitProvenance:
    source: OrbitSource
    file_path: Path
    coverage_start: datetime
    coverage_end: datetime
    satellite: str
    checksum_sha256: str


def validate_orbit_coverage(
    coverage_start: datetime,
    coverage_end: datetime,
    burst_start: datetime,
    burst_end: datetime,
) -> None:
    timestamps = (
        coverage_start,
        coverage_end,
        burst_start,
        burst_end,
    )

    if any(
        timestamp.tzinfo is None or timestamp.utcoffset() is None
        for timestamp in timestamps
    ):
        raise ValueError("Orbit coverage timestamps must be timezone-aware")

    if coverage_start >= coverage_end:
        raise ValueError("Invalid orbit coverage interval")

    if burst_start > burst_end:
        raise ValueError("Invalid burst interval")

    if not coverage_start <= burst_start <= burst_end <= coverage_end:
        raise ValueError("Selected burst is not fully covered by the orbit product")
