import json

import pytest

from utils.geojson_manage import GeojsonManager

# ---------------------------------------
# Fixtures
# ---------------------------------------


@pytest.fixture
def polygon_data():
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [-16.0, 28.0],
                [-15.0, 28.0],
                [-15.0, 29.0],
                [-16.0, 29.0],
                [-16.0, 28.0],
            ]
        ],
    }


@pytest.fixture
def geojson_file(tmp_path, polygon_data):
    file_path = tmp_path / "aoi.geojson"

    file_path.write_text(
        json.dumps(polygon_data),
        encoding="utf-8",
    )

    return file_path


@pytest.fixture
def manager(geojson_file):
    return GeojsonManager(geojson_file)


# ---------------------------------------
# read_geojson()
# ---------------------------------------


def test_read_geojson_success(manager, polygon_data):
    result = manager.read_geojson()

    assert result == polygon_data


@pytest.mark.parametrize(
    "invalid_type",
    [
        "Point",
        "LineString",
        "MultiPolygon",
        "Feature",
        "FeatureCollection",
        None,
    ],
)
def test_read_geojson_invalid_type(
    tmp_path,
    invalid_type,
):
    file_path = tmp_path / "invalid.geojson"

    file_path.write_text(
        json.dumps(
            {
                "type": invalid_type,
                "coordinates": [],
            }
        ),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(
        ValueError,
        match="Invalid AOI GeoJSON: expected Polygon",
    ):
        manager.read_geojson()


def test_read_geojson_missing_type(tmp_path):
    file_path = tmp_path / "missing_type.geojson"

    file_path.write_text(
        json.dumps(
            {
                "coordinates": [],
            }
        ),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(
        ValueError,
        match="Invalid AOI GeoJSON: expected Polygon",
    ):
        manager.read_geojson()


def test_read_geojson_invalid_json(tmp_path):
    file_path = tmp_path / "invalid.geojson"

    file_path.write_text(
        '{"type": "Polygon", invalid}',
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(json.JSONDecodeError):
        manager.read_geojson()


def test_read_geojson_file_not_found(tmp_path):
    file_path = tmp_path / "missing.geojson"

    manager = GeojsonManager(file_path)

    with pytest.raises(FileNotFoundError):
        manager.read_geojson()


def test_read_geojson_empty_file(tmp_path):
    file_path = tmp_path / "empty.geojson"

    file_path.write_text("", encoding="utf-8")

    manager = GeojsonManager(file_path)

    with pytest.raises(json.JSONDecodeError):
        manager.read_geojson()


# ---------------------------------------
# polygon_to_wkt()
# ---------------------------------------


def test_polygon_to_wkt_success(manager):
    result = manager.polygon_to_wkt()

    expected = "POLYGON ((-16.0 28.0, -15.0 28.0, -15.0 29.0, -16.0 29.0, -16.0 28.0))"

    assert result == expected


def test_polygon_to_wkt_with_hole(tmp_path):
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [0, 0],
                [10, 0],
                [10, 10],
                [0, 10],
                [0, 0],
            ],
            [
                [2, 2],
                [4, 2],
                [4, 4],
                [2, 4],
                [2, 2],
            ],
        ],
    }

    file_path = tmp_path / "polygon_with_hole.geojson"

    file_path.write_text(
        json.dumps(polygon),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    result = manager.polygon_to_wkt()

    expected = "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 4 2, 4 4, 2 4, 2 2))"

    assert result == expected


def test_polygon_to_wkt_unclosed_exterior_ring(tmp_path):
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [0, 0],
                [1, 0],
                [1, 1],
                [0, 1],
            ]
        ],
    }

    file_path = tmp_path / "unclosed.geojson"

    file_path.write_text(
        json.dumps(polygon),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(
        ValueError,
        match="Polygon ring must be closed",
    ):
        manager.polygon_to_wkt()


def test_polygon_to_wkt_unclosed_interior_ring(tmp_path):
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [0, 0],
                [10, 0],
                [10, 10],
                [0, 10],
                [0, 0],
            ],
            [
                [2, 2],
                [4, 2],
                [4, 4],
                [2, 4],
            ],
        ],
    }

    file_path = tmp_path / "unclosed_hole.geojson"

    file_path.write_text(
        json.dumps(polygon),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(
        ValueError,
        match="Polygon ring must be closed",
    ):
        manager.polygon_to_wkt()


def test_polygon_to_wkt_rejects_non_polygon(tmp_path):
    data = {
        "type": "Point",
        "coordinates": [0, 0],
    }

    file_path = tmp_path / "point.geojson"

    file_path.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    manager = GeojsonManager(file_path)

    with pytest.raises(
        ValueError,
        match="Invalid AOI GeoJSON: expected Polygon",
    ):
        manager.polygon_to_wkt()
