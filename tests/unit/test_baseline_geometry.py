import numpy as np
import pytest

from utils.baseline_geometry import calculate_baseline_geometry


def test_known_baseline_geometry():
    result = calculate_baseline_geometry(
        reference_position=np.array([0.0, 0.0, 0.0]),
        secondary_position=np.array([3.0, 4.0, 0.0]),
        ground_position=np.array([10.0, 0.0, 0.0]),
    )

    assert result.total_m == pytest.approx(5.0)
    assert result.parallel_m == pytest.approx(3.0)
    assert result.perpendicular_m == pytest.approx(4.0)


def test_identical_satellite_positions():
    result = calculate_baseline_geometry(
        reference_position=np.array([0.0, 0.0, 0.0]),
        secondary_position=np.array([0.0, 0.0, 0.0]),
        ground_position=np.array([10.0, 0.0, 0.0]),
    )

    assert result.total_m == pytest.approx(0.0)
    assert result.parallel_m == pytest.approx(0.0)
    assert result.perpendicular_m == pytest.approx(0.0)


def test_baseline_parallel_to_los():
    result = calculate_baseline_geometry(
        reference_position=np.array([0.0, 0.0, 0.0]),
        secondary_position=np.array([5.0, 0.0, 0.0]),
        ground_position=np.array([10.0, 0.0, 0.0]),
    )

    assert result.total_m == pytest.approx(5.0)
    assert result.parallel_m == pytest.approx(5.0)
    assert result.perpendicular_m == pytest.approx(0.0)


def test_baseline_opposite_to_los():
    result = calculate_baseline_geometry(
        reference_position=np.array([0.0, 0.0, 0.0]),
        secondary_position=np.array([-5.0, 0.0, 0.0]),
        ground_position=np.array([10.0, 0.0, 0.0]),
    )

    assert result.total_m == pytest.approx(5.0)
    assert result.parallel_m == pytest.approx(-5.0)
    assert result.perpendicular_m == pytest.approx(0.0)


def test_baseline_perpendicular_to_los():
    result = calculate_baseline_geometry(
        reference_position=np.array([0.0, 0.0, 0.0]),
        secondary_position=np.array([0.0, 5.0, 0.0]),
        ground_position=np.array([10.0, 0.0, 0.0]),
    )

    assert result.total_m == pytest.approx(5.0)
    assert result.parallel_m == pytest.approx(0.0)
    assert result.perpendicular_m == pytest.approx(5.0)


def test_baseline_decomposition():
    result = calculate_baseline_geometry(
        reference_position=np.array([100.0, 200.0, 300.0]),
        secondary_position=np.array([103.0, 204.0, 312.0]),
        ground_position=np.array([1100.0, 200.0, 300.0]),
    )

    assert result.total_m**2 == pytest.approx(
        result.parallel_m**2 + result.perpendicular_m**2,
        rel=1e-10,
        abs=1e-6,
    )


@pytest.mark.parametrize(
    "invalid_position",
    [
        np.array([1.0, 2.0]),
        np.array([1.0, 2.0, np.nan]),
        np.array([1.0, 2.0, np.inf]),
    ],
)
def test_invalid_reference_position(invalid_position):
    with pytest.raises(ValueError):
        calculate_baseline_geometry(
            reference_position=invalid_position,
            secondary_position=np.array([3.0, 4.0, 0.0]),
            ground_position=np.array([10.0, 0.0, 0.0]),
        )


def test_zero_los_distance():
    with pytest.raises(ValueError, match="identical positions"):
        calculate_baseline_geometry(
            reference_position=np.array([1.0, 2.0, 3.0]),
            secondary_position=np.array([4.0, 5.0, 6.0]),
            ground_position=np.array([1.0, 2.0, 3.0]),
        )
