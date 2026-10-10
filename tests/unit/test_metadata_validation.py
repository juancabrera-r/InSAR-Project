import logging
from copy import deepcopy

import pytest

from utils.metadata_validation import MetadataValidator

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def logger():
    return logging.getLogger("test_metadata_validation")


@pytest.fixture
def config():
    return {
        "SAR_PARAMETERS": {
            "PLATFORM": "A",
            "ORBIT_DIRECTION": "ASCENDING",
            "RELATIVE_ORBIT": 1,
            "SWATH": "IW3",
        },
        "COPERNICUS_PRODUCT": {
            "POLARIZATION": "VV VH",
        },
    }


@pytest.fixture
def make_burst():
    def _make_burst(burst_id, **overrides):
        burst = {
            "BurstId": burst_id,
            "AbsoluteBurstId": burst_id + 1000,
            "SwathIdentifier": "IW3",
            "PlatformSerialIdentifier": "A",
            "OrbitDirection": "ASCENDING",
            "RelativeOrbitNumber": 1,
            "PolarisationChannels": "VV VH",
        }
        burst.update(overrides)
        return burst

    return _make_burst


@pytest.fixture
def bursts_by_product(make_burst):
    return {
        "product1": [
            make_burst(101),
            make_burst(102),
            make_burst(103),
        ],
        "product2": [
            make_burst(101),
            make_burst(102),
            make_burst(103),
        ],
    }


@pytest.fixture
def validator(bursts_by_product, config, logger):
    return MetadataValidator(
        bursts_by_product=deepcopy(bursts_by_product),
        config=deepcopy(config),
        logger=logger,
    )


# ============================================================
# 1. INITIALIZATION
# ============================================================


def test_initialization(validator, config):
    assert validator.platform == config["SAR_PARAMETERS"]["PLATFORM"]
    assert validator.orbit_direction == config["SAR_PARAMETERS"]["ORBIT_DIRECTION"]
    assert validator.relative_orbit == config["SAR_PARAMETERS"]["RELATIVE_ORBIT"]
    assert validator.swath == config["SAR_PARAMETERS"]["SWATH"]
    assert validator.polarisation == config["COPERNICUS_PRODUCT"]["POLARIZATION"]


@pytest.mark.parametrize(
    "section,field",
    [
        ("SAR_PARAMETERS", "PLATFORM"),
        ("SAR_PARAMETERS", "ORBIT_DIRECTION"),
        ("SAR_PARAMETERS", "RELATIVE_ORBIT"),
        ("SAR_PARAMETERS", "SWATH"),
        ("COPERNICUS_PRODUCT", "POLARIZATION"),
    ],
)
def test_initialization_missing_config_field(config, logger, section, field):
    del config[section][field]

    with pytest.raises(KeyError, match=field):
        MetadataValidator(
            bursts_by_product={},
            config=config,
            logger=logger,
        )


# ============================================================
# 2. _require_nonempty_bursts_by_product
# ============================================================


def test_require_nonempty_bursts_by_product(validator):
    assert validator._require_nonempty_bursts_by_product() is None


@pytest.mark.parametrize(
    "empty_value",
    [
        {},
        None,
    ],
)
def test_require_nonempty_bursts_by_product_empty(validator, empty_value):
    validator.bursts_by_product = empty_value

    with pytest.raises(
        ValueError,
        match="No acquisitions available for validation",
    ):
        validator._require_nonempty_bursts_by_product()


def test_require_nonempty_bursts_by_product_empty_product(validator):
    validator.bursts_by_product = {"product1": []}

    # Current implementation checks only the outer dictionary.
    assert validator._require_nonempty_bursts_by_product() is None


# ============================================================
# 3. inspect_burst_identity
# ============================================================


def test_inspect_burst_identity(validator):
    result = validator.inspect_burst_identity()

    assert set(result) == {"product1", "product2"}

    assert result["product1"] == [
        {
            "burst_id": 101,
            "absolute_burst_id": 1101,
            "swath": "IW3",
        },
        {
            "burst_id": 102,
            "absolute_burst_id": 1102,
            "swath": "IW3",
        },
        {
            "burst_id": 103,
            "absolute_burst_id": 1103,
            "swath": "IW3",
        },
    ]

    assert result["product2"] == result["product1"]


def test_inspect_burst_identity_single_product(validator):
    validator.bursts_by_product = {"product1": validator.bursts_by_product["product1"]}

    result = validator.inspect_burst_identity()

    assert len(result) == 1
    assert len(result["product1"]) == 3


def test_inspect_burst_identity_empty_acquisitions(validator):
    validator.bursts_by_product = {}

    with pytest.raises(
        ValueError,
        match="No acquisitions available for validation",
    ):
        validator.inspect_burst_identity()


def test_inspect_burst_identity_empty_product(validator):
    validator.bursts_by_product = {"product1": []}

    assert validator.inspect_burst_identity() == {"product1": []}


@pytest.mark.parametrize(
    "missing_field",
    [
        "BurstId",
        "AbsoluteBurstId",
        "SwathIdentifier",
    ],
)
def test_inspect_burst_identity_missing_field(validator, missing_field):
    del validator.bursts_by_product["product1"][0][missing_field]

    with pytest.raises(KeyError, match=missing_field):
        validator.inspect_burst_identity()


def test_inspect_burst_identity_preserves_duplicates(validator, make_burst):
    validator.bursts_by_product = {
        "product1": [
            make_burst(101),
            make_burst(101),
        ]
    }

    result = validator.inspect_burst_identity()

    assert len(result["product1"]) == 2
    assert result["product1"][0]["burst_id"] == 101
    assert result["product1"][1]["burst_id"] == 101


# ============================================================
# 4. validate_burst_identity
# ============================================================


def test_validate_burst_identity_consistent(validator):
    result = validator.validate_burst_identity()

    assert result["reference_product_id"] == "product1"
    assert result["reference_burst_ids"] == [101, 102, 103]
    assert result["all_consistent"] is True

    for product in result["products"].values():
        assert product["burst_ids"] == [101, 102, 103]
        assert product["missing"] == []
        assert product["unexpected"] == []
        assert product["has_duplicates"] is False
        assert product["is_consistent"] is True


def test_validate_burst_identity_missing_burst(validator, make_burst):
    validator.bursts_by_product["product2"] = [
        make_burst(101),
        make_burst(102),
    ]

    result = validator.validate_burst_identity()
    product = result["products"]["product2"]

    assert product["missing"] == [103]
    assert product["unexpected"] == []
    assert product["has_duplicates"] is False
    assert product["is_consistent"] is False
    assert result["all_consistent"] is False


def test_validate_burst_identity_unexpected_burst(validator, make_burst):
    validator.bursts_by_product["product2"].append(make_burst(104))

    result = validator.validate_burst_identity()
    product = result["products"]["product2"]

    assert product["missing"] == []
    assert product["unexpected"] == [104]
    assert product["is_consistent"] is False
    assert result["all_consistent"] is False


def test_validate_burst_identity_duplicate_burst(validator, make_burst):
    validator.bursts_by_product["product2"].append(make_burst(101))

    result = validator.validate_burst_identity()
    product = result["products"]["product2"]

    assert product["burst_ids"] == [101, 102, 103]
    assert product["missing"] == []
    assert product["unexpected"] == []
    assert product["has_duplicates"] is True
    assert product["is_consistent"] is False
    assert result["all_consistent"] is False


def test_validate_burst_identity_missing_and_unexpected(validator, make_burst):
    validator.bursts_by_product["product2"] = [
        make_burst(101),
        make_burst(104),
    ]

    result = validator.validate_burst_identity()
    product = result["products"]["product2"]

    assert product["missing"] == [102, 103]
    assert product["unexpected"] == [104]
    assert product["is_consistent"] is False


def test_validate_burst_identity_different_order(validator):
    validator.bursts_by_product["product2"].reverse()

    result = validator.validate_burst_identity()

    assert result["all_consistent"] is True


def test_validate_burst_identity_empty_acquisitions(validator):
    validator.bursts_by_product = {}

    with pytest.raises(
        ValueError,
        match="No acquisitions available for validation",
    ):
        validator.validate_burst_identity()


def test_validate_burst_identity_empty_product(validator):
    validator.bursts_by_product["product2"] = []

    result = validator.validate_burst_identity()
    product = result["products"]["product2"]

    assert product["burst_ids"] == []
    assert product["missing"] == [101, 102, 103]
    assert product["unexpected"] == []
    assert product["is_consistent"] is False
    assert result["all_consistent"] is False


def test_validate_burst_identity_empty_reference(validator, make_burst):
    validator.bursts_by_product = {
        "product1": [],
        "product2": [make_burst(101)],
    }

    result = validator.validate_burst_identity()

    assert result["reference_burst_ids"] == []
    assert result["products"]["product1"]["is_consistent"] is True
    assert result["products"]["product2"]["unexpected"] == [101]
    assert result["all_consistent"] is False


def test_validate_burst_identity_all_products_empty(validator):
    validator.bursts_by_product = {
        "product1": [],
        "product2": [],
    }

    result = validator.validate_burst_identity()

    # Documents current behavior, not necessarily desired behavior.
    assert result["all_consistent"] is True
    assert result["reference_burst_ids"] == []


def test_validate_burst_identity_missing_burst_id(validator):
    del validator.bursts_by_product["product2"][0]["BurstId"]

    with pytest.raises(KeyError, match="BurstId"):
        validator.validate_burst_identity()


# ============================================================
# 5. validate_acquisition_metadata
# ============================================================


def test_validate_acquisition_metadata_valid(validator):
    result = validator.validate_acquisition_metadata()

    assert result == []


@pytest.mark.parametrize(
    "field,invalid_value,expected_value",
    [
        ("PlatformSerialIdentifier", "C", "A"),
        ("OrbitDirection", "DESCENDING", "ASCENDING"),
        ("RelativeOrbitNumber", 2, 1),
        ("SwathIdentifier", "IW2", "IW3"),
        ("PolarisationChannels", "VV", "VV VH"),
    ],
)
def test_validate_acquisition_metadata_mismatch(
    validator, field, invalid_value, expected_value
):
    validator.bursts_by_product["product2"][0][field] = invalid_value

    result = validator.validate_acquisition_metadata()

    assert result == [
        {
            "product_id": "product2",
            "burst_id": 101,
            "field": field,
            "expected": expected_value,
            "actual": invalid_value,
        }
    ]


def test_validate_acquisition_metadata_multiple_mismatches(validator):
    burst = validator.bursts_by_product["product2"][0]

    burst["PlatformSerialIdentifier"] = "C"
    burst["OrbitDirection"] = "DESCENDING"
    burst["RelativeOrbitNumber"] = 2

    result = validator.validate_acquisition_metadata()

    assert len(result) == 3

    assert {item["field"] for item in result} == {
        "PlatformSerialIdentifier",
        "OrbitDirection",
        "RelativeOrbitNumber",
    }

    assert all(
        item["product_id"] == "product2" and item["burst_id"] == 101 for item in result
    )


@pytest.mark.parametrize(
    "missing_field,expected_value",
    [
        ("PlatformSerialIdentifier", "A"),
        ("OrbitDirection", "ASCENDING"),
        ("RelativeOrbitNumber", 1),
        ("SwathIdentifier", "IW3"),
        ("PolarisationChannels", "VV VH"),
    ],
)
def test_validate_acquisition_metadata_missing_field(
    validator, missing_field, expected_value
):
    del validator.bursts_by_product["product1"][0][missing_field]

    result = validator.validate_acquisition_metadata()

    assert result == [
        {
            "product_id": "product1",
            "burst_id": 101,
            "field": missing_field,
            "expected": expected_value,
            "actual": None,
        }
    ]


def test_validate_acquisition_metadata_missing_burst_id(validator):
    del validator.bursts_by_product["product1"][0]["BurstId"]

    result = validator.validate_acquisition_metadata()

    # BurstId is used for reporting, not validated as metadata.
    assert result == []


def test_validate_acquisition_metadata_missing_burst_id_with_mismatch(validator):
    burst = validator.bursts_by_product["product1"][0]

    del burst["BurstId"]
    burst["OrbitDirection"] = "DESCENDING"

    result = validator.validate_acquisition_metadata()

    assert result == [
        {
            "product_id": "product1",
            "burst_id": None,
            "field": "OrbitDirection",
            "expected": "ASCENDING",
            "actual": "DESCENDING",
        }
    ]


def test_validate_acquisition_metadata_empty_acquisitions(validator):
    validator.bursts_by_product = {}

    with pytest.raises(
        ValueError,
        match="No acquisitions available for validation",
    ):
        validator.validate_acquisition_metadata()


def test_validate_acquisition_metadata_empty_product(validator):
    validator.bursts_by_product = {"product1": []}

    assert validator.validate_acquisition_metadata() == []


def test_validate_acquisition_metadata_type_mismatch(validator):
    validator.bursts_by_product["product1"][0]["RelativeOrbitNumber"] = "1"

    result = validator.validate_acquisition_metadata()

    assert len(result) == 1
    assert result[0]["field"] == "RelativeOrbitNumber"
    assert result[0]["expected"] == 1
    assert result[0]["actual"] == "1"


def test_validate_acquisition_metadata_does_not_mutate_input(validator):
    original = deepcopy(validator.bursts_by_product)

    validator.validate_acquisition_metadata()

    assert validator.bursts_by_product == original
