from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from insar_platform.geometry.orbit_models import OrbitSource


@dataclass(frozen=True)
class ExternalOrbit:
    source: OrbitSource
    file_path: Path
    satellite: str
    validity_start: datetime
    validity_end: datetime
    vectors: list[dict]


def parse_utc(value: str) -> datetime:
    value = value.strip().removeprefix("UTC=")
    timestamp = datetime.fromisoformat(value)

    # Sentinel-1 EOF timestamps are expressed in UTC.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    return timestamp.astimezone(UTC)


def read_external_orbit(file_path: Path) -> ExternalOrbit:
    root = ET.parse(file_path).getroot()

    def required_text(path: str) -> str:
        value = root.findtext(path)
        if value is None or not value.strip():
            raise ValueError(f"Missing {path} in {file_path}")
        return value.strip()

    file_type = required_text("./Earth_Explorer_Header/Fixed_Header/File_Type")

    if "POEORB" in file_type:
        source = OrbitSource.POEORB
    elif "RESORB" in file_type:
        source = OrbitSource.RESORB
    else:
        raise ValueError(f"Unsupported orbit type: {file_type}")

    mission = required_text("./Earth_Explorer_Header/Fixed_Header/Mission")
    satellite = (
        mission.replace("Sentinel-", "S")
        .replace("Sentinel", "S")
        .replace(" ", "")
        .upper()
    )

    if satellite not in {"S1A", "S1B", "S1C", "S1D"}:
        raise ValueError(f"Unsupported satellite: {mission}")

    validity_start = parse_utc(
        required_text(
            "./Earth_Explorer_Header/Fixed_Header/Validity_Period/Validity_Start"
        )
    )
    validity_end = parse_utc(
        required_text(
            "./Earth_Explorer_Header/Fixed_Header/Validity_Period/Validity_Stop"
        )
    )

    if validity_start >= validity_end:
        raise ValueError("Invalid external orbit validity interval")

    vectors = []

    for osv in root.findall("./Data_Block/List_of_OSVs/OSV"):
        timestamp_text = osv.findtext("UTC")
        if timestamp_text is None:
            raise ValueError("Missing OSV timestamp")

        position = {}
        velocity = {}

        for axis in ("X", "Y", "Z"):
            position[axis.lower()] = _required_float(osv, axis)
            velocity[axis.lower()] = _required_float(osv, f"V{axis}")

        values = list(position.values()) + list(velocity.values())
        if not np.all(np.isfinite(values)):
            raise ValueError("Non-finite external orbit state vector")

        vectors.append(
            {
                "timestamp": parse_utc(timestamp_text),
                "position": position,
                "velocity": velocity,
            }
        )

    if len(vectors) < 2:
        raise ValueError("External orbit requires at least two state vectors")

    vectors.sort(key=lambda vector: vector["timestamp"])

    if any(
        first["timestamp"] >= second["timestamp"] for first, second in pairwise(vectors)
    ):
        raise ValueError("Duplicate or invalid orbit timestamps")

    return ExternalOrbit(
        source=source,
        file_path=file_path,
        satellite=satellite,
        validity_start=validity_start,
        validity_end=validity_end,
        vectors=vectors,
    )


def _required_float(element: ET.Element, tag: str) -> float:
    value = element.findtext(tag)

    if value is None or not value.strip():
        raise ValueError(f"Missing required XML field: {tag}")

    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid numeric value for XML field {tag}: {value!r}"
        ) from exc

    if not np.isfinite(number):
        raise ValueError(f"Non-finite XML value for {tag}: {value!r}")

    return number
