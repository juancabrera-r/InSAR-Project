from datetime import UTC, datetime, timedelta
from itertools import pairwise
from logging import Logger
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET

import numpy as np
from scipy.optimize import brentq

from insar_platform.geometry.coordinate_transform import geodetic_to_ecef
from insar_platform.utils.date_time_format import to_iso_utc


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
            # self.logger.debug(
            #     "Checking orbit vector interval: t1=%s, t2=%s",
            #     t1,
            #     t2,
            # )

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

        if not np.all(np.isfinite(position)):
            raise ValueError(
                "Interpolated satellite position contains non-finite values"
            )

        if not np.all(np.isfinite(velocity)):
            raise ValueError(
                "Interpolated satellite velocity contains non-finite values"
            )

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

            latitude = self._required_float(point, "latitude")
            longitude = self._required_float(point, "longitude")

            if not -90.0 <= latitude <= 90.0:
                raise ValueError(f"Invalid latitude: {latitude}")

            if not -180.0 <= longitude <= 180.0:
                raise ValueError(f"Invalid longitude: {longitude}")

            points.append(
                {
                    "azimuth_time": azimuth_time,
                    "slant_range_time": self._required_float(point, "slantRangeTime"),
                    "latitude": latitude,
                    "longitude": longitude,
                    "height": self._required_float(point, "height"),
                    "line": int(line_text),
                    "pixel": int(pixel_text),
                }
            )

        return points

    def find_zero_doppler_brackets(
        self,
        orbit_vectors: list[dict],
        ground_position: np.ndarray,
    ) -> list[tuple[dict, dict]]:
        brackets = []

        if len(orbit_vectors) < 2:
            raise ValueError("At least two orbit vectors are required")

        timestamps = [vector["timestamp"] for vector in orbit_vectors]

        if any(t1 >= t2 for t1, t2 in pairwise(timestamps)):
            raise ValueError("Orbit timestamps must be strictly increasing")

        for first, second in pairwise(orbit_vectors):
            p1 = np.array(
                [first["position"][axis] for axis in ("x", "y", "z")],
                dtype=float,
            )
            v1 = np.array(
                [first["velocity"][axis] for axis in ("x", "y", "z")],
                dtype=float,
            )

            p2 = np.array(
                [second["position"][axis] for axis in ("x", "y", "z")],
                dtype=float,
            )
            v2 = np.array(
                [second["velocity"][axis] for axis in ("x", "y", "z")],
                dtype=float,
            )

            residual_first = self.zero_doppler_residual(p1, v1, ground_position)
            residual_second = self.zero_doppler_residual(p2, v2, ground_position)

            if not np.isfinite(residual_first) or not np.isfinite(residual_second):
                raise ValueError("Non-finite zero-Doppler residual")

            if (
                residual_first == 0.0
                or residual_second == 0.0
                or np.signbit(residual_first) != np.signbit(residual_second)
            ):
                brackets.append((first, second))

        return brackets

    def solve_zero_doppler_time(
        self,
        first: dict,
        second: dict,
        ground_position: np.ndarray,
    ) -> datetime:
        start_time = first["timestamp"]
        duration = (second["timestamp"] - start_time).total_seconds()

        if duration <= 0:
            raise ValueError("Invalid zero-Doppler bracket")

        def residual_at_seconds(seconds: float) -> float:
            candidate_time = start_time + timedelta(seconds=seconds)

            position, velocity = self.interpolate_state(
                first,
                second,
                candidate_time,
            )

            return self.zero_doppler_residual(
                satellite_position=position,
                satellite_velocity=velocity,
                ground_position=ground_position,
            )

        f_start = residual_at_seconds(0.0)
        f_end = residual_at_seconds(duration)

        if not np.isfinite(f_start) or not np.isfinite(f_end):
            raise ValueError("Non-finite zero-Doppler residual")

        if f_start == 0.0:
            return start_time

        if f_end == 0.0:
            return second["timestamp"]

        if np.signbit(f_start) == np.signbit(f_end):
            raise ValueError("Zero-Doppler bracket does not contain a sign change")

        root_seconds = cast(
            float,
            brentq(
                residual_at_seconds,
                0.0,
                duration,
                xtol=1e-6,
                full_output=False,
            ),
        )
        return start_time + timedelta(seconds=root_seconds)

    @staticmethod
    def zero_doppler_residual(
        satellite_position: np.ndarray,
        satellite_velocity: np.ndarray,
        ground_position: np.ndarray,
    ) -> float:
        line_of_sight = ground_position - satellite_position

        return float(np.dot(line_of_sight, satellite_velocity))

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

    @staticmethod
    def select_central_geolocation_point(points):
        if not points:
            raise ValueError("Geolocation grid is empty")

        lines = [point["line"] for point in points]
        pixels = [point["pixel"] for point in points]

        min_line, max_line = min(lines), max(lines)
        min_pixel, max_pixel = min(pixels), max(pixels)

        line_span = max_line - min_line
        pixel_span = max_pixel - min_pixel

        if line_span == 0 or pixel_span == 0:
            raise ValueError("Geolocation grid has a zero-length dimension")

        center_line = (min_line + max_line) / 2
        center_pixel = (min_pixel + max_pixel) / 2

        return min(
            points,
            key=lambda point: (
                ((point["line"] - center_line) / line_span) ** 2
                + ((point["pixel"] - center_pixel) / pixel_span) ** 2
            ),
        )

    @staticmethod
    def interpolate_geolocation_point(
        points: list[dict],
        target_line: float,
        target_pixel: float,
    ) -> np.ndarray:
        from scipy.interpolate import LinearNDInterpolator

        image_coordinates = np.array(
            [[p["line"], p["pixel"]] for p in points],
            dtype=float,
        )

        ground_coordinates = np.array(
            [
                geodetic_to_ecef(
                    latitude=p["latitude"],
                    longitude=p["longitude"],
                    height=p["height"],
                )
                for p in points
            ],
            dtype=float,
        )

        interpolator = LinearNDInterpolator(
            image_coordinates,
            ground_coordinates,
            fill_value=np.nan,
        )

        position = np.asarray(
            interpolator(target_line, target_pixel),
            dtype=float,
        ).reshape(3)

        if not np.all(np.isfinite(position)):
            raise ValueError(
                "Unable to interpolate reference ground position "
                "inside geolocation-grid coverage"
            )

        return position

    @staticmethod
    def extract_satellite(xml_file_path: Path) -> str:
        if not xml_file_path.is_file():
            raise FileNotFoundError(f"Annotation XML not found: {xml_file_path}")

        root = ET.parse(xml_file_path).getroot()

        satellite = root.findtext("./adsHeader/missionId")

        if satellite is None:
            raise ValueError("Missing adsHeader/missionId in annotation XML")

        satellite = satellite.strip()

        if satellite not in {"S1A", "S1B", "S1C", "S1D"}:
            raise ValueError(f"Unsupported Sentinel-1 satellite: {satellite}")

        return satellite

    @staticmethod
    def extract_measurement_width(xml_file_path: Path) -> int:
        root = ET.parse(xml_file_path).getroot()

        value = root.findtext("./imageAnnotation/imageInformation/numberOfSamples")

        if value is None:
            raise ValueError(
                f"Missing numberOfSamples in annotation XML: {xml_file_path}"
            )

        measurement_width = int(value)

        if measurement_width <= 0:
            raise ValueError(f"Invalid measurement width: {measurement_width}")

        return measurement_width
