import logging

import pytest
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping
from shapely.ops import transform

from utils.spatial_validation import ValidateSpatial


@pytest.fixture
def logger():
    return logging.getLogger("test_spatial_validation")


@pytest.fixture
def to_wgs84():
    transformer = Transformer.from_crs(
        "EPSG:25830",
        "EPSG:4326",
        always_xy=True,
    )
    return transformer.transform


@pytest.fixture
def aoi_wkt(to_wgs84):
    aoi = box(500000, 4400000, 501000, 4401000)
    return transform(to_wgs84, aoi).wkt


@pytest.fixture
def bursts_by_product(to_wgs84):
    product1 = box(500000, 4400000, 500750, 4401000)
    product2 = box(500250, 4400000, 501000, 4401000)

    return {
        "product1": [{"GeoFootprint": mapping(transform(to_wgs84, product1))}],
        "product2": [{"GeoFootprint": mapping(transform(to_wgs84, product2))}],
    }


@pytest.fixture
def ingestion(logger, bursts_by_product, aoi_wkt):
    return ValidateSpatial(
        logger=logger,
        bursts_by_product=bursts_by_product,
        aoi_wkt=aoi_wkt,
    )


def test_require_nonempty_bursts_by_product_empty(ingestion):
    ingestion.bursts_by_product = {}

    with pytest.raises(ValueError, match="No acquisitions available for validation"):
        ingestion._require_nonempty_bursts_by_product()


def test_require_nonempty_bursts_by_product(ingestion, bursts_by_product):

    ingestion.bursts_by_product = bursts_by_product

    assert ingestion._require_nonempty_bursts_by_product() is None


def test_quantify_coverage(ingestion):
    result = ingestion.quantify_coverage()

    acquisitions = result["acquisitions"]

    assert len(acquisitions) == 2

    assert acquisitions["product1"]["coverage_percentage"] == pytest.approx(
        75.0, abs=1e-5
    )

    assert acquisitions["product2"]["coverage_percentage"] == pytest.approx(
        75.0, abs=1e-5
    )

    assert result["common_coverage_percentage"] == pytest.approx(50.0, abs=1e-5)


def test_quantify_coverage_empty_aoi(ingestion):
    ingestion.aoi_wkt = "POLYGON EMPTY"

    with pytest.raises(ValueError, match="AOI geometry is invalid or empty"):
        ingestion.quantify_coverage()


def test_quantify_coverage_zero_area_aoi(ingestion):
    ingestion.aoi_wkt = "POINT (-3.0 40.0)"

    with pytest.raises(
        ValueError, match="AOI projected area must be greater than zero"
    ):
        ingestion.quantify_coverage()


def test_quantify_coverage_empty_acquisitions(ingestion):
    ingestion.bursts_by_product = {}

    with pytest.raises(
        ValueError,
        match="No acquisitions available for validation",
    ):
        ingestion.quantify_coverage()


def test_quantify_coverage_empty_product(ingestion):
    ingestion.bursts_by_product = {"product1": []}

    with pytest.raises(
        ValueError, match="Invalid or missing burst geometries for product product1"
    ):
        ingestion.quantify_coverage()


def test_quantify_coverage_invariants(ingestion):
    result = ingestion.quantify_coverage()

    percentages = [
        acquisition["coverage_percentage"]
        for acquisition in result["acquisitions"].values()
    ]

    common = result["common_coverage_percentage"]

    assert all(0 <= percentage <= 100 for percentage in percentages)

    assert 0 <= common <= 100

    assert common <= min(percentages) + 1e-8


def test_quantify_coverage_invalid_geometry(ingestion):
    invalid_geometry = Polygon(
        [
            (0, 0),
            (1, 1),
            (1, 0),
            (0, 1),
            (0, 0),
        ]
    )

    ingestion.bursts_by_product = {
        "product1": [{"GeoFootprint": mapping(invalid_geometry)}]
    }

    with pytest.raises(
        ValueError,
        match="Invalid or missing burst geometries for product product1",
    ):
        ingestion.quantify_coverage()
