from datetime import UTC, datetime, timedelta
from logging import getLogger
from pathlib import Path

import numpy as np
import pytest

from utils.calculate_orbit import CalculateOrbit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_DIR = PROJECT_ROOT / "data" / "annotation"

ANNOTATION_PATHS = [
    ANNOTATION_DIR
    / "s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.xml",
    ANNOTATION_DIR
    / "s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.xml",
]

EARTH_RADIUS = 6_371_000.0
T0 = datetime(2025, 1, 1, 12, tzinfo=UTC)


@pytest.fixture
def calculator():
    return CalculateOrbit(logger=getLogger("test.calculate_orbit"))


@pytest.fixture
def orbit_vectors():
    return [
        {
            "timestamp": T0 + timedelta(seconds=i * 10),
            "position": {"x": x, "y": i * 75_000.0, "z": 0.0},
            "velocity": {"x": -80.0 * i, "y": 7_500.0, "z": 0.0},
        }
        for i, x in enumerate((7_071_000.0, 7_070_600.0, 7_069_400.0))
    ]


@pytest.mark.parametrize("annotation_path", ANNOTATION_PATHS)
def test_annotation_velocity_consistency(annotation_path):
    if not annotation_path.is_file():
        pytest.skip(f"Annotation file unavailable: {annotation_path}")

    calculator = CalculateOrbit(logger=getLogger("test.orbit_validation"))

    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)

    assert len(orbit_vectors) >= 3

    axes = ("x", "y", "z")
    differences = []

    for index in range(1, len(orbit_vectors) - 1):
        previous = orbit_vectors[index - 1]
        current = orbit_vectors[index]
        following = orbit_vectors[index + 1]

        p_previous = np.array(
            [previous["position"][axis] for axis in axes],
            dtype=float,
        )

        p_following = np.array(
            [following["position"][axis] for axis in axes],
            dtype=float,
        )

        v_annotation = np.array(
            [current["velocity"][axis] for axis in axes],
            dtype=float,
        )

        dt = (following["timestamp"] - previous["timestamp"]).total_seconds()

        assert dt > 0

        v_finite_difference = (p_following - p_previous) / dt

        difference = np.linalg.norm(v_annotation - v_finite_difference)

        assert np.isfinite(difference)
        differences.append(difference)

    differences = np.asarray(differences)

    print(
        f"\n{annotation_path.name}"
        f"\nMean velocity difference: {differences.mean():.6f} m/s"
        f"\nMaximum velocity difference: {differences.max():.6f} m/s"
    )


@pytest.mark.parametrize("annotation_path", ANNOTATION_PATHS)
def test_annotation_velocity_consistency_fourth_order(annotation_path):
    if not annotation_path.is_file():
        pytest.skip(f"Annotation file unavailable: {annotation_path}")

    calculator = CalculateOrbit(logger=getLogger("test.orbit_validation"))

    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)

    assert len(orbit_vectors) >= 3

    axes = ("x", "y", "z")
    five_point_differences = []

    for index in range(2, len(orbit_vectors) - 2):
        positions = [
            np.array(
                [orbit_vectors[j]["position"][axis] for axis in axes],
                dtype=float,
            )
            for j in range(index - 2, index + 3)
        ]

        dt = (
            orbit_vectors[index + 1]["timestamp"] - orbit_vectors[index]["timestamp"]
        ).total_seconds()

        v_five_point = (
            positions[0] - 8 * positions[1] + 8 * positions[3] - positions[4]
        ) / (12 * dt)

        v_annotation = np.array(
            [orbit_vectors[index]["velocity"][axis] for axis in axes],
            dtype=float,
        )

        five_point_differences.append(np.linalg.norm(v_annotation - v_five_point))

    print(
        "Maximum five-point velocity difference:",
        max(five_point_differences),
        "m/s",
    )

    max_difference = max(five_point_differences)

    # Threshold 0.01 m/s for maximum allowable velocity difference
    assert max_difference < 0.01, (
        f"Orbit position/velocity inconsistency: {max_difference:.6f} m/s"
    )


@pytest.fixture(params=ANNOTATION_PATHS, ids=lambda path: path.stem)
def real_orbit_vectors(request, calculator):
    annotation_path = request.param

    assert annotation_path.is_file(), (
        f"Required annotation file not found: {annotation_path}"
    )

    vectors = calculator.extract_orbit_xml_file(annotation_path)

    assert len(vectors) >= 3

    return vectors


def test_interpolated_state_physical_plausibility(calculator, real_orbit_vectors):

    first, second = real_orbit_vectors[:2]

    midpoint = first["timestamp"] + (second["timestamp"] - first["timestamp"]) / 2

    position, velocity = calculator.interpolate_state(
        first,
        second,
        midpoint,
    )

    assert np.all(np.isfinite(position))
    assert np.all(np.isfinite(velocity))
