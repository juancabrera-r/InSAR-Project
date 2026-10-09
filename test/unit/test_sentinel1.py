import hashlib
import logging
from unittest.mock import patch

import pytest
import requests

from ingestion.sentinel1 import Sentinel1Ingestion


# ------------------------------
# Test for bursts fingerprint
# ------------------------------
@pytest.fixture
def base_config():
    return {
        "START_DATE": "2025-01-01 00:00:00",
        "END_DATE": "2025-01-31 00:00:00",
        "URL": "https://example.com",
        "SAR_PARAMETERS": {
            "PLATFORM": "A",
            "ORBIT_DIRECTION": "ASCENDING",
            "RELATIVE_ORBIT": 1,
            "SWATH": "IW3",
        },
        "COPERNICUS_PRODUCT": {
            "AREA": "geography",
            "PARENTPRODUCTTYPE": "SLC",
            "POLARIZATION": "VV",
        },
        "AOI_WKT": "some_wkt",
    }


@pytest.fixture
def sample_bursts():
    return [
        {"Id": "burst1"},
        {"Id": "burst2"},
        {"Id": "burst3"},
    ]


@pytest.fixture
def ingestion(base_config):
    return Sentinel1Ingestion(
        base_config,
        aoi_wkt="some_wkt",
    )


# ------------------------------
# Parametrize test
# ------------------------------
@pytest.mark.parametrize(
    "method_name",
    ["get_bursts_dates", "get_id", "get_deduplicated_id", "_bursts_fingerprint"],
)
def test_require_nonempty_bursts(caplog, ingestion, method_name):
    caplog.set_level(logging.INFO)

    method = getattr(ingestion, method_name)

    with pytest.raises(ValueError, match="No bursts provided"):
        method([])


# ------------------------------
# Test for first page
# ------------------------------
@patch("ingestion.sentinel1.requests.get")
def test_request_first_page_success(mock_get, ingestion):
    url = "https://example.com/bursts"
    filter_expression = "PlatformSerialIdentifier eq 'A'"

    expected_payload = {
        "value": [
            {"Id": "burst1"},
            {"Id": "burst2"},
        ]
    }

    mock_get.return_value.json.return_value = expected_payload

    result = ingestion.request_first_page(
        url,
        filter_expression,
    )

    assert result == expected_payload

    mock_get.assert_called_once_with(
        url,
        params={
            "$filter": filter_expression,
            "$orderby": "Id asc",
        },
        timeout=30,
    )

    mock_get.return_value.raise_for_status.assert_called_once_with()


@patch("ingestion.sentinel1.requests.get")
def test_request_first_page_http_error(mock_get, ingestion):
    url = "https://example.com/bursts"
    filter_expression = "PlatformSerialIdentifier eq 'A'"

    mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "HTTP Error"
    )

    with pytest.raises(requests.HTTPError, match="HTTP Error"):
        ingestion.request_first_page(
            url,
            filter_expression,
        )


# ------------------------------
# Test for next page
# ------------------------------
@patch("ingestion.sentinel1.requests.get")
def test_request_next_page_success(mock_get, ingestion):
    url = "https://example.com/bursts?page=2"

    expected_payload = {
        "value": [
            {"Id": "burst1"},
            {"Id": "burst2"},
        ]
    }

    mock_get.return_value.json.return_value = expected_payload

    result = ingestion.request_next_page(url)

    assert result == expected_payload

    mock_get.assert_called_once_with(
        url,
        timeout=30,
    )

    mock_get.return_value.raise_for_status.assert_called_once_with()


@patch("ingestion.sentinel1.requests.get")
def test_request_next_page_http_error(mock_get, ingestion):
    url = "https://example.com/bursts?page=2"

    mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "HTTP Error"
    )

    with pytest.raises(requests.HTTPError, match="HTTP Error"):
        ingestion.request_next_page(url)


# ------------------------------
# Test for search_bursts
# ------------------------------
def test_search_bursts_success(ingestion):
    # Arrange
    first_page = {
        "value": [
            {"Id": "burst1"},
            {"Id": "burst2"},
        ],
        "@odata.nextLink": "https://example.com/page2",
    }

    second_page = {
        "value": [
            {"Id": "burst3"},
        ],
    }

    expected = [
        {"Id": "burst1"},
        {"Id": "burst2"},
        {"Id": "burst3"},
    ]

    with (
        patch.object(
            ingestion,
            "request_first_page",
            return_value=first_page,
        ) as mock_first,
        patch.object(
            ingestion,
            "request_next_page",
            return_value=second_page,
        ) as mock_next,
        patch.object(
            ingestion,
            "_bursts_fingerprint",
        ) as mock_fingerprint,
    ):
        # Act
        result = ingestion.search_bursts()

    # Assert
    assert result == expected

    mock_first.assert_called_once()
    mock_next.assert_called_once_with("https://example.com/page2")
    mock_fingerprint.assert_called_once_with(expected)

    args, _ = mock_first.call_args

    assert args[0] == ingestion.initial_url

    filter_expression = args[1]

    assert "ContentDate/Start ge" in filter_expression
    assert "ContentDate/Start lt" in filter_expression
    assert "ParentProductType eq 'SLC'" in filter_expression
    assert "PolarisationChannels eq 'VV'" in filter_expression
    assert "SRID=4326;some_wkt" in filter_expression


# ------------------------------
# Test for get bursts dates
# ------------------------------
def make_burst(
    date: str,
    platform: str = "A",
    orbit_direction: str = "ASCENDING",
    relative_orbit: int = 1,
    swath: str = "IW3",
) -> dict:
    return {
        "PlatformSerialIdentifier": platform,
        "OrbitDirection": orbit_direction,
        "RelativeOrbitNumber": relative_orbit,
        "SwathIdentifier": swath,
        "ContentDate": {"Start": date},
    }


def test_get_bursts_dates_returns_sorted_dates(ingestion):

    bursts = [
        make_burst("2025-03-01"),
        make_burst("2025-01-01"),
        make_burst("2025-02-01"),
    ]

    dates_result = ingestion.get_bursts_dates(bursts)

    dates = ["2025-01-01", "2025-02-01", "2025-03-01"]

    assert dates_result == dates


def test_get_bursts_dates_filters_metadata(ingestion):

    bursts = [
        make_burst("2025-01-01"),
        make_burst("2025-01-02", platform="B"),
        make_burst("2025-01-03", orbit_direction="DESCENDING"),
        make_burst("2025-01-04", relative_orbit=2),
        make_burst("2025-01-05", swath="IW2"),
    ]
    dates_result = ingestion.get_bursts_dates(bursts)

    dates = ["2025-01-01"]

    assert dates_result == dates


def test_get_bursts_dates_deduplication_dates(ingestion):

    bursts = [
        make_burst("2025-01-01T06:00:00Z"),
        make_burst("2025-01-01T18:00:00Z"),
        make_burst("2025-01-13T06:00:00Z"),
    ]

    dates_result = ingestion.get_bursts_dates(bursts)

    dates = ["2025-01-01", "2025-01-13"]

    assert dates_result == dates


def test_get_bursts_dates_no_matching(ingestion):

    bursts = [
        make_burst("2025-01-01", platform="B"),
    ]

    dates_result = ingestion.get_bursts_dates(bursts)

    dates = []

    assert dates_result == dates


# ------------------------------
# Test for get id
# ------------------------------
def test_get_id_unique(ingestion, sample_bursts):
    expected = {
        "total": 3,
        "unique": 3,
        "duplicates": 0,
    }

    result = ingestion.get_id(sample_bursts)

    assert result == expected


def test_get_id_duplicates(ingestion, sample_bursts):
    bursts = sample_bursts.copy()
    bursts.append({"Id": "burst1"})

    expected = {
        "total": 4,
        "unique": 3,
        "duplicates": 1,
    }

    result = ingestion.get_id(bursts)

    assert result != expected


# ------------------------------
# Test for bursts fingerprint
# ------------------------------
def test_bursts_fingerprint_matches(
    caplog,
    base_config,
    sample_bursts,
    ingestion,
):

    burst_ids = sorted(burst["Id"] for burst in sample_bursts)

    expected_fingerprint = hashlib.sha256(
        "\n".join(burst_ids).encode("utf-8")
    ).hexdigest()

    base_config["BURSTS_FINGERPRINT"] = expected_fingerprint

    caplog.set_level(logging.INFO)

    ingestion._bursts_fingerprint(sample_bursts)

    assert "Fingerprint matches the original" in caplog.text


def test_bursts_fingerprint_mismatch(caplog, base_config, sample_bursts, ingestion):
    bursts_mismatch = [
        {"Id": "burst1"},
        {"Id": "burst2"},
        {"Id": "burst3_modified"},
    ]

    burst_ids = sorted(burst["Id"] for burst in sample_bursts)

    expected_fingerprint = hashlib.sha256(
        "\n".join(burst_ids).encode("utf-8")
    ).hexdigest()

    base_config["BURSTS_FINGERPRINT"] = expected_fingerprint

    caplog.set_level(logging.INFO)

    ingestion._bursts_fingerprint(bursts_mismatch)

    assert any(
        record.levelno == logging.WARNING
        and record.message == "Fingerprint does not match the original"
        for record in caplog.records
    )
