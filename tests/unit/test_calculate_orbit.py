from datetime import UTC, datetime, timedelta
from logging import getLogger
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from src.insar_platform.geometry.calculate_orbit import CalculateOrbit

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


@pytest.fixture
def write_orbit_xml(tmp_path):
    def write(orbits):
        root = ET.Element("root")
        orbit_list = ET.SubElement(root, "orbitList")
        for orbit_data in orbits:
            orbit = ET.SubElement(orbit_list, "orbit")
            if orbit_data.get("time", "") is not None:
                ET.SubElement(orbit, "time").text = orbit_data.get("time", "")
            for name, default in (
                ("position", {"x": 1, "y": 2, "z": 3}),
                ("velocity", {"x": 4, "y": 5, "z": 6}),
            ):
                parent = ET.SubElement(orbit, name)
                for axis, value in orbit_data.get(name, default).items():
                    ET.SubElement(parent, axis).text = str(value)
        path = tmp_path / "orbit.xml"
        ET.ElementTree(root).write(path, encoding="utf-8")
        return path

    return write


@pytest.fixture
def write_grid_xml(tmp_path):
    def write(points):
        root = ET.Element("root")
        point_list = ET.SubElement(root, "geolocationGridPointList")
        for values in points:
            point = ET.SubElement(point_list, "geolocationGridPoint")
            for key, value in values.items():
                ET.SubElement(point, key).text = str(value)
        path = tmp_path / "grid.xml"
        ET.ElementTree(root).write(path, encoding="utf-8")
        return path

    return write


@pytest.fixture
def grid_point():
    return {
        "azimuthTime": "2025-01-01T12:00:00Z",
        "slantRangeTime": "0.005",
        "latitude": "28.123",
        "longitude": "-15.456",
        "height": "123.4",
        "line": "10",
        "pixel": "20",
    }


# XML orbit extraction


def test_extract_orbit_xml_success(calculator, write_orbit_xml):
    path = write_orbit_xml(
        [
            {"time": "2025-01-01T12:00:00"},
            {"time": "2025-01-01T12:00:10"},
        ]
    )
    vectors = calculator.extract_orbit_xml_file(path)
    assert len(vectors) == 2
    assert vectors[0]["timestamp"] == T0
    assert vectors[0]["position"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert vectors[0]["velocity"] == {"x": 4.0, "y": 5.0, "z": 6.0}


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2025-01-01T12:00:00", T0),
        ("2025-01-01T12:00:00Z", T0),
        ("2025-01-01T12:00:00+00:00", T0),
        ("2025-01-01T14:00:00+02:00", T0),
        ("2025-01-01T07:00:00-05:00", T0),
        ("2025-01-01T12:00:00.123456Z", T0 + timedelta(microseconds=123456)),
    ],
)
def test_extract_orbit_xml_timezone_conversion(
    calculator, write_orbit_xml, timestamp, expected
):
    vectors = calculator.extract_orbit_xml_file(write_orbit_xml([{"time": timestamp}]))
    assert vectors[0]["timestamp"] == expected
    assert vectors[0]["timestamp"].tzinfo == UTC


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_required_float_rejects_non_finite(value):
    element = ET.fromstring(f"<root><x>{value}</x></root>")

    with pytest.raises(ValueError, match="Non-finite XML value"):
        CalculateOrbit._required_float(element, "x")


def test_extract_orbit_xml_file_not_found(calculator, tmp_path):
    with pytest.raises(FileNotFoundError, match="XML file not found"):
        calculator.extract_orbit_xml_file(tmp_path / "missing.xml")


def test_extract_orbit_xml_no_vectors(calculator, write_orbit_xml):
    with pytest.raises(ValueError, match="No orbit vectors found"):
        calculator.extract_orbit_xml_file(write_orbit_xml([]))


def test_extract_orbit_xml_missing_timestamp(calculator, write_orbit_xml):
    with pytest.raises(ValueError, match="Missing orbit timestamp"):
        calculator.extract_orbit_xml_file(write_orbit_xml([{"time": None}]))


@pytest.mark.parametrize(
    "timestamp",
    [
        "invalid",
        "2025-02-30T12:00:00",
        "2025-13-01T12:00:00",
        "2025-01-01T25:00:00",
    ],
)
def test_extract_orbit_xml_invalid_timestamp(calculator, write_orbit_xml, timestamp):
    with pytest.raises(ValueError):
        calculator.extract_orbit_xml_file(write_orbit_xml([{"time": timestamp}]))


@pytest.mark.parametrize(
    ("field", "axis"),
    [(field, axis) for field in ("position", "velocity") for axis in ("x", "y", "z")],
)
def test_extract_orbit_xml_missing_coordinate(calculator, write_orbit_xml, field, axis):
    values = {"x": 1, "y": 2, "z": 3}
    del values[axis]
    path = write_orbit_xml([{"time": "2025-01-01T12:00:00", field: values}])
    with pytest.raises(ValueError, match=f"Missing required XML field: {field}/{axis}"):
        calculator.extract_orbit_xml_file(path)


@pytest.mark.parametrize("invalid_value", ["abc", "", " ", "not-a-number"])
def test_extract_orbit_xml_invalid_coordinate(
    calculator, write_orbit_xml, invalid_value
):
    path = write_orbit_xml(
        [
            {
                "time": "2025-01-01T12:00:00",
                "position": {"x": invalid_value, "y": 2, "z": 3},
            }
        ]
    )
    expected = (
        "Missing required XML field"
        if not invalid_value.strip()
        else "Invalid numeric value"
    )
    with pytest.raises(ValueError, match=expected):
        calculator.extract_orbit_xml_file(path)


def test_extract_orbit_xml_malformed_xml(calculator, tmp_path):
    path = tmp_path / "invalid.xml"
    path.write_text("<root><orbitList><orbit>", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        calculator.extract_orbit_xml_file(path)


# Bracketing


@pytest.mark.parametrize(
    ("seconds", "indices"),
    [
        (0, (0, 1)),
        (5, (0, 1)),
        (10, (0, 1)),
        (15, (1, 2)),
        (20, (1, 2)),
    ],
)
def test_find_bracketing_vectors(calculator, orbit_vectors, seconds, indices):
    first, second = calculator.find_bracketing_vectors(
        orbit_vectors, T0 + timedelta(seconds=seconds)
    )
    assert first is orbit_vectors[indices[0]]
    assert second is orbit_vectors[indices[1]]


@pytest.mark.parametrize("seconds", [-1, 21])
def test_find_bracketing_vectors_outside_coverage(calculator, orbit_vectors, seconds):
    with pytest.raises(ValueError, match="outside the orbit vector coverage"):
        calculator.find_bracketing_vectors(
            orbit_vectors, T0 + timedelta(seconds=seconds)
        )


@pytest.mark.parametrize("count", [0, 1])
def test_find_bracketing_vectors_insufficient_vectors(calculator, orbit_vectors, count):
    with pytest.raises(ValueError, match="At least two orbit vectors"):
        calculator.find_bracketing_vectors(orbit_vectors[:count], T0)


@pytest.mark.parametrize(
    "acquisition_time",
    [
        T0 + timedelta(days=1),
        T0 - timedelta(days=1),
    ],
)
def test_find_bracketing_vectors_outside_coverage_by_one_day(
    calculator,
    orbit_vectors,
    acquisition_time,
):
    with pytest.raises(
        ValueError,
        match="Acquisition time is outside the orbit vector coverage",
    ):
        calculator.find_bracketing_vectors(
            orbit_vectors,
            acquisition_time,
        )


def test_find_bracketing_vectors_midnight_crossing(calculator):
    start = datetime(2025, 1, 1, 23, 59, 55, tzinfo=UTC)

    vectors = [
        {"timestamp": start},
        {"timestamp": start + timedelta(seconds=10)},
    ]

    first, second = calculator.find_bracketing_vectors(
        vectors,
        start + timedelta(seconds=7),
    )

    assert first is vectors[0]
    assert second is vectors[1]


def test_find_bracketing_vectors_duplicate_timestamps(calculator, orbit_vectors):
    vectors = [orbit_vectors[0], {**orbit_vectors[1], "timestamp": T0}]
    with pytest.raises(ValueError, match="strictly increasing"):
        calculator.find_bracketing_vectors(vectors, T0)


def test_find_bracketing_vectors_unsorted(calculator, orbit_vectors):
    with pytest.raises(ValueError, match="strictly increasing"):
        calculator.find_bracketing_vectors([orbit_vectors[1], orbit_vectors[0]], T0)


# Hermite interpolation


@pytest.mark.parametrize("endpoint", [0, 1])
def test_interpolate_state_endpoints(calculator, orbit_vectors, endpoint):
    first, second = orbit_vectors[:2]
    target = (first, second)[endpoint]
    result, _ = calculator.interpolate_state(first, second, target["timestamp"])
    np.testing.assert_allclose(
        result,
        [target["position"][axis] for axis in ("x", "y", "z")],
        rtol=0,
        atol=1e-8,
    )


def test_interpolate_state_linear_motion(calculator):
    first = {
        "timestamp": T0,
        "position": {"x": 100, "y": 200, "z": 300},
        "velocity": {"x": 10, "y": 20, "z": 30},
    }
    second = {
        "timestamp": T0 + timedelta(seconds=10),
        "position": {"x": 200, "y": 400, "z": 600},
        "velocity": {"x": 10, "y": 20, "z": 30},
    }
    np.testing.assert_allclose(
        calculator.interpolate_state(first, second, T0 + timedelta(seconds=5))[0],
        [150, 300, 450],
        rtol=0,
        atol=1e-10,
    )


def test_interpolate_state_cubic_polynomial(calculator):
    first = {
        "timestamp": T0,
        "position": {"x": 0, "y": 0, "z": 0},
        "velocity": {"x": 0, "y": 0, "z": 0},
    }
    second = {
        "timestamp": T0 + timedelta(seconds=10),
        "position": {"x": 1000, "y": 2000, "z": -1000},
        "velocity": {"x": 300, "y": 600, "z": -300},
    }
    np.testing.assert_allclose(
        calculator.interpolate_state(first, second, T0 + timedelta(seconds=5))[0],
        [125, 250, -125],
        rtol=0,
        atol=1e-10,
    )


def test_interpolate_state_returns_numpy_array(calculator, orbit_vectors):
    result, _ = calculator.interpolate_state(
        *orbit_vectors[:2], T0 + timedelta(seconds=5)
    )
    assert isinstance(result, np.ndarray)
    assert result.shape == (3,)
    assert np.issubdtype(result.dtype, np.floating)
    assert np.all(np.isfinite(result))


def test_interpolate_state_invalid_interval(calculator, orbit_vectors):
    first, second = orbit_vectors[:2]
    with pytest.raises(ValueError, match="Invalid orbit time interval"):
        calculator.interpolate_state(first, {**second, "timestamp": T0}, T0)


def test_interpolate_state_reversed_interval(calculator, orbit_vectors):
    with pytest.raises(ValueError, match="Invalid orbit time interval"):
        calculator.interpolate_state(
            orbit_vectors[1], orbit_vectors[0], T0 + timedelta(seconds=5)
        )


@pytest.mark.parametrize("seconds", [-1, 11])
def test_interpolate_state_outside_interval(calculator, orbit_vectors, seconds):
    with pytest.raises(ValueError, match="must be between the two vectors"):
        calculator.interpolate_state(
            *orbit_vectors[:2], T0 + timedelta(seconds=seconds)
        )


def test_interpolated_state_physical_plausibility(calculator, orbit_vectors):
    position = calculator.interpolate_state(
        *orbit_vectors[:2], T0 + timedelta(seconds=5)
    )[0]
    radius = np.linalg.norm(position)
    altitude = radius - EARTH_RADIUS
    assert np.isfinite(radius)
    assert np.isfinite(altitude)
    assert 600_000 <= altitude <= 800_000
    assert altitude == pytest.approx(700_000, abs=10_000)


# Geolocation grid extraction


def test_extract_geolocation_grid_success(calculator, write_grid_xml, grid_point):
    points = calculator.extract_geolocation_grid(write_grid_xml([grid_point]))
    assert points == [
        {
            "azimuth_time": "2025-01-01T12:00:00Z",
            "slant_range_time": 0.005,
            "latitude": 28.123,
            "longitude": -15.456,
            "height": 123.4,
            "line": 10,
            "pixel": 20,
        }
    ]


def test_extract_geolocation_grid_multiple_points(
    calculator, write_grid_xml, grid_point
):
    other = {**grid_point, "line": "20", "pixel": "30", "latitude": "29.0"}
    points = calculator.extract_geolocation_grid(write_grid_xml([grid_point, other]))
    assert len(points) == 2
    assert [p["line"] for p in points] == [10, 20]
    assert [p["pixel"] for p in points] == [20, 30]
    assert points[1]["latitude"] == 29.0


def test_extract_geolocation_grid_empty(calculator, write_grid_xml):
    assert calculator.extract_geolocation_grid(write_grid_xml([])) == []


@pytest.mark.parametrize("field", ["azimuthTime", "line", "pixel"])
def test_extract_geolocation_grid_missing_required_text(
    calculator, write_grid_xml, grid_point, field
):
    values = {key: value for key, value in grid_point.items() if key != field}
    expected = (
        "Missing azimuthTime" if field == "azimuthTime" else "Missing line or pixel"
    )
    with pytest.raises(ValueError, match=expected):
        calculator.extract_geolocation_grid(write_grid_xml([values]))


@pytest.mark.parametrize("field", ["slantRangeTime", "latitude", "longitude", "height"])
def test_extract_geolocation_grid_missing_float(
    calculator, write_grid_xml, grid_point, field
):
    values = {key: value for key, value in grid_point.items() if key != field}
    with pytest.raises(ValueError, match=f"Missing required XML field: {field}"):
        calculator.extract_geolocation_grid(write_grid_xml([values]))


@pytest.mark.parametrize("field", ["slantRangeTime", "latitude", "longitude", "height"])
@pytest.mark.parametrize("value", ["abc", "", "  "])
def test_extract_geolocation_grid_invalid_float(
    calculator, write_grid_xml, grid_point, field, value
):
    expected = (
        "Missing required XML field" if not value.strip() else "Invalid numeric value"
    )
    with pytest.raises(ValueError, match=expected):
        calculator.extract_geolocation_grid(
            write_grid_xml([{**grid_point, field: value}])
        )


@pytest.mark.parametrize("field", ["line", "pixel"])
@pytest.mark.parametrize("value", ["abc", "1.5", ""])
def test_extract_geolocation_grid_invalid_index(
    calculator, write_grid_xml, grid_point, field, value
):
    with pytest.raises(ValueError):
        calculator.extract_geolocation_grid(
            write_grid_xml([{**grid_point, field: value}])
        )


def test_extract_geolocation_grid_malformed_xml(calculator, tmp_path):
    path = tmp_path / "bad_grid.xml"
    path.write_text("<root><geolocationGridPointList>", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        calculator.extract_geolocation_grid(path)


def test_extract_geolocation_grid_file_not_found(calculator, tmp_path):
    with pytest.raises(FileNotFoundError):
        calculator.extract_geolocation_grid(tmp_path / "missing.xml")


# Numeric parsing


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123.45", 123.45),
        ("-123.45", -123.45),
        ("0", 0.0),
        ("1.25e3", 1250.0),
        ("-2.5E-2", -0.025),
    ],
)
def test_required_float_valid(value, expected):
    element = ET.fromstring(f"<root><x>{value}</x></root>")
    assert CalculateOrbit._required_float(element, "x") == pytest.approx(expected)


@pytest.mark.parametrize(
    "xml", ["<root/>", "<root><x/></root>", "<root><x>  </x></root>"]
)
def test_required_float_missing(xml):
    with pytest.raises(ValueError, match="Missing required XML field: x"):
        CalculateOrbit._required_float(ET.fromstring(xml), "x")


@pytest.mark.parametrize("value", ["abc", "1,5", "not-a-number"])
def test_required_float_invalid(value):
    with pytest.raises(ValueError, match="Invalid numeric value for XML field x"):
        CalculateOrbit._required_float(
            ET.fromstring(f"<root><x>{value}</x></root>"), "x"
        )
