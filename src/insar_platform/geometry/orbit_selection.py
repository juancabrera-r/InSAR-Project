from datetime import datetime
from pathlib import Path

from insar_platform.geometry.external_orbit import (
    ExternalOrbit,
    read_external_orbit,
)
from insar_platform.geometry.orbit_models import OrbitSource


def select_external_orbit(
    orbit_directory: Path,
    satellite: str,
    burst_start: datetime,
    burst_end: datetime,
) -> ExternalOrbit | None:

    candidates: list[ExternalOrbit] = []

    for file_path in orbit_directory.rglob("*.EOF"):
        orbit = read_external_orbit(file_path)

        if orbit.satellite != satellite:
            continue

        if not (
            orbit.validity_start <= burst_start and burst_end <= orbit.validity_end
        ):
            continue

        # Ensure the actual state vectors cover the burst.
        if not (
            orbit.vectors[0]["timestamp"] <= burst_start
            and burst_end <= orbit.vectors[-1]["timestamp"]
        ):
            continue

        candidates.append(orbit)

    priority = {
        OrbitSource.POEORB: 0,
        OrbitSource.RESORB: 1,
    }

    candidates.sort(
        key=lambda orbit: (
            priority[orbit.source],
            -orbit.validity_start.timestamp(),
            str(orbit.file_path),
        )
    )

    return candidates[0] if candidates else None
