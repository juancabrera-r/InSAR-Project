from datetime import UTC, datetime
from itertools import pairwise
from logging import Logger
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from src.utils.date_time_format import to_iso_utc


class CalculateOrbit:
    def __init__(self, logger: Logger) -> None:
        self.logger = logger

    def extract_orbit_xml_file(self, xml_file_path: Path) -> list[dict]:
        if not xml_file_path.is_file():
            raise FileNotFoundError(f"XML file not found: {xml_file_path}")

        root = ET.parse(xml_file_path).getroot()
        orbit_vectors = []

        for orbit in root.findall(".//orbitList/orbit"):
            timestamp = orbit.findtext("time")

            if timestamp is None:
                raise ValueError("Missing orbit timestamp")

            orbit_datetime = datetime.fromisoformat(to_iso_utc(timestamp))

            if orbit_datetime.tzinfo is None:
                raise ValueError("Orbit timestamp must include timezone information")

            orbit_datetime = orbit_datetime.astimezone(UTC)

            position = {
                axis: self._required_float(orbit, f"position/{axis}")
                for axis in ("x", "y", "z")
            }

            velocity = {
                axis: self._required_float(orbit, f"velocity/{axis}")
                for axis in ("x", "y", "z")
            }

            orbit_vectors.append(
                {
                    "timestamp": orbit_datetime,
                    "position": position,
                    "velocity": velocity,
                }
            )

        if not orbit_vectors:
            raise ValueError("No orbit vectors found")

        return orbit_vectors

    def find_bracketing_vectors(
        self,
        orbit_vectors: list[dict],
        acquisition_time: datetime,
    ) -> tuple[dict, dict]:

        if len(orbit_vectors) < 2:
            raise ValueError("At least two orbit vectors are required")

        timestamps = [vector["timestamp"] for vector in orbit_vectors]

        if any(t1 >= t2 for t1, t2 in pairwise(timestamps)):
            raise ValueError("Orbit timestamps must be strictly increasing")

        for first, second in pairwise(orbit_vectors):
            t1 = first["timestamp"]
            t2 = second["timestamp"]
            self.logger.debug(
                "Checking orbit vector interval: t1=%s, t2=%s",
                t1,
                t2,
            )

            if t1 <= acquisition_time <= t2:
                return first, second

        raise ValueError("Acquisition time is outside the orbit vector coverage")

    def interpolate_state(
        self,
        first: dict,
        second: dict,
        acquisition_time: datetime,
    ) -> tuple[np.ndarray, np.ndarray]:

        t1 = first["timestamp"]
        t2 = second["timestamp"]

        delta_t = (t2 - t1).total_seconds()

        if delta_t <= 0:
            raise ValueError("Invalid orbit time interval")

        u = (acquisition_time - t1).total_seconds() / delta_t

        if not 0 <= u <= 1:
            raise ValueError("Acquisition time must be between the two vectors")

        p1 = np.array(
            [first["position"][axis] for axis in ("x", "y", "z")],
            dtype=float,
        )
        p2 = np.array(
            [second["position"][axis] for axis in ("x", "y", "z")],
            dtype=float,
        )

        v1 = np.array(
            [first["velocity"][axis] for axis in ("x", "y", "z")],
            dtype=float,
        )
        v2 = np.array(
            [second["velocity"][axis] for axis in ("x", "y", "z")],
            dtype=float,
        )

        h00 = 2 * u**3 - 3 * u**2 + 1
        h10 = u**3 - 2 * u**2 + u
        h01 = -2 * u**3 + 3 * u**2
        h11 = u**3 - u**2

        position = h00 * p1 + h10 * delta_t * v1 + h01 * p2 + h11 * delta_t * v2

        dh00 = 6 * u**2 - 6 * u
        dh10 = 3 * u**2 - 4 * u + 1
        dh01 = -6 * u**2 + 6 * u
        dh11 = 3 * u**2 - 2 * u

        velocity = (dh00 * p1 + dh01 * p2) / delta_t + dh10 * v1 + dh11 * v2

        return position, velocity

    def extract_geolocation_grid(
        self,
        xml_file_path: Path,
    ) -> list[dict]:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()

        points = []

        for point in root.findall(".//geolocationGridPointList/geolocationGridPoint"):
            azimuth_time = point.findtext("azimuthTime")
            line_text = point.findtext("line")
            pixel_text = point.findtext("pixel")

            if azimuth_time is None:
                raise ValueError("Missing azimuthTime in geolocation grid")

            if line_text is None or pixel_text is None:
                raise ValueError("Missing line or pixel in geolocation grid")

            points.append(
                {
                    "azimuth_time": azimuth_time,
                    "slant_range_time": self._required_float(point, "slantRangeTime"),
                    "latitude": self._required_float(point, "latitude"),
                    "longitude": self._required_float(point, "longitude"),
                    "height": self._required_float(point, "height"),
                    "line": int(line_text),
                    "pixel": int(pixel_text),
                }
            )

        return points

    @staticmethod
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
