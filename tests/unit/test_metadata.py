from collections import Counter
from copy import deepcopy

import pytest

from ingestion.metadata import metadata_summary

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def bursts():
    return [
        {
            "BurstId": 101,
            "PlatformSerialIdentifier": "A",
            "OrbitDirection": "ASCENDING",
            "RelativeOrbitNumber": 1,
            "SwathIdentifier": "IW3",
        },
        {
            "BurstId": 102,
            "PlatformSerialIdentifier": "A",
            "OrbitDirection": "ASCENDING",
            "RelativeOrbitNumber": 1,
            "SwathIdentifier": "IW3",
        },
        {
            "BurstId": 103,
            "PlatformSerialIdentifier": "C",
            "OrbitDirection": "DESCENDING",
            "RelativeOrbitNumber": 2,
            "SwathIdentifier": "IW2",
        },
    ]


# ============================================================
# 1. NORMAL BEHAVIOR
# ============================================================


def test_metadata_summary(bursts):
    result = metadata_summary(bursts)

    assert result == {
        "platform_counts": Counter(
            {
                "A": 2,
                "C": 1,
            }
        ),
        "track_counts": Counter(
            {
                ("ASCENDING", 1, "IW3"): 2,
                ("DESCENDING", 2, "IW2"): 1,
            }
        ),
    }


def test_metadata_summary_return_types(bursts):
    result = metadata_summary(bursts)

    assert isinstance(result, dict)
    assert isinstance(result["platform_counts"], Counter)
    assert isinstance(result["track_counts"], Counter)


def test_metadata_summary_single_burst():
    bursts = [
        {
            "PlatformSerialIdentifier": "A",
            "OrbitDirection": "ASCENDING",
            "RelativeOrbitNumber": 1,
            "SwathIdentifier": "IW3",
        }
    ]

    result = metadata_summary(bursts)

    assert result["platform_counts"] == Counter({"A": 1})
    assert result["track_counts"] == Counter({("ASCENDING", 1, "IW3"): 1})


# ============================================================
# 2. EMPTY INPUT
# ============================================================


def test_metadata_summary_empty_list():
    result = metadata_summary([])

    assert result == {
        "platform_counts": Counter(),
        "track_counts": Counter(),
    }


# ============================================================
# 3. COUNTING BEHAVIOR
# ============================================================


def test_metadata_summary_duplicate_bursts(bursts):
    bursts.append(deepcopy(bursts[0]))

    result = metadata_summary(bursts)

    assert result["platform_counts"]["A"] == 3
    assert result["track_counts"][("ASCENDING", 1, "IW3")] == 3


def test_metadata_summary_same_platform_different_tracks(bursts):
    bursts[2]["PlatformSerialIdentifier"] = "A"

    result = metadata_summary(bursts)

    assert result["platform_counts"] == Counter({"A": 3})

    assert result["track_counts"] == Counter(
        {
            ("ASCENDING", 1, "IW3"): 2,
            ("DESCENDING", 2, "IW2"): 1,
        }
    )


def test_metadata_summary_different_platforms_same_track(bursts):
    bursts[2]["OrbitDirection"] = "ASCENDING"
    bursts[2]["RelativeOrbitNumber"] = 1
    bursts[2]["SwathIdentifier"] = "IW3"

    result = metadata_summary(bursts)

    assert result["platform_counts"] == Counter(
        {
            "A": 2,
            "C": 1,
        }
    )

    assert result["track_counts"] == Counter(
        {
            ("ASCENDING", 1, "IW3"): 3,
        }
    )


@pytest.mark.parametrize(
    "field,new_value,expected_track",
    [
        (
            "OrbitDirection",
            "DESCENDING",
            ("DESCENDING", 1, "IW3"),
        ),
        (
            "RelativeOrbitNumber",
            5,
            ("ASCENDING", 5, "IW3"),
        ),
        (
            "SwathIdentifier",
            "IW1",
            ("ASCENDING", 1, "IW1"),
        ),
    ],
)
def test_metadata_summary_track_field_changes(bursts, field, new_value, expected_track):
    bursts[0][field] = new_value

    result = metadata_summary(bursts)

    assert result["track_counts"][expected_track] == 1
    assert result["track_counts"][("ASCENDING", 1, "IW3")] == 1


# ============================================================
# 4. MISSING METADATA
# ============================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "PlatformSerialIdentifier",
        "OrbitDirection",
        "RelativeOrbitNumber",
        "SwathIdentifier",
    ],
)
def test_metadata_summary_missing_field(bursts, missing_field):
    del bursts[0][missing_field]

    with pytest.raises(KeyError, match=missing_field):
        metadata_summary(bursts)


def test_metadata_summary_empty_burst_dictionary():
    with pytest.raises(KeyError, match="PlatformSerialIdentifier"):
        metadata_summary([{}])


# ============================================================
# 5. INVALID INPUT TYPES
# ============================================================


def test_metadata_summary_none_input():
    with pytest.raises(TypeError):
        metadata_summary(None)


def test_metadata_summary_non_dictionary_burst():
    with pytest.raises(TypeError):
        metadata_summary([123])


def test_metadata_summary_unhashable_platform(bursts):
    bursts[0]["PlatformSerialIdentifier"] = ["A"]

    with pytest.raises(TypeError):
        metadata_summary(bursts)


@pytest.mark.parametrize(
    "field",
    [
        "OrbitDirection",
        "RelativeOrbitNumber",
        "SwathIdentifier",
    ],
)
def test_metadata_summary_unhashable_track_field(bursts, field):
    bursts[0][field] = ["invalid"]

    with pytest.raises(TypeError):
        metadata_summary(bursts)


# ============================================================
# 6. SPECIAL VALUES
# ============================================================


def test_metadata_summary_none_metadata_values(bursts):
    bursts[0]["PlatformSerialIdentifier"] = None
    bursts[0]["OrbitDirection"] = None

    result = metadata_summary(bursts)

    assert result["platform_counts"][None] == 1
    assert result["track_counts"][(None, 1, "IW3")] == 1


def test_metadata_summary_string_integer_orbit(bursts):
    bursts[0]["RelativeOrbitNumber"] = "1"

    result = metadata_summary(bursts)

    assert result["track_counts"][("ASCENDING", "1", "IW3")] == 1
    assert result["track_counts"][("ASCENDING", 1, "IW3")] == 1


# ============================================================
# 7. INPUT IMMUTABILITY
# ============================================================


def test_metadata_summary_does_not_modify_input(bursts):
    original = deepcopy(bursts)

    metadata_summary(bursts)

    assert bursts == original


# ============================================================
# 8. COUNT INVARIANTS
# ============================================================


def test_metadata_summary_total_counts(bursts):
    result = metadata_summary(bursts)

    assert sum(result["platform_counts"].values()) == len(bursts)
    assert sum(result["track_counts"].values()) == len(bursts)


def test_metadata_summary_expected_keys(bursts):
    result = metadata_summary(bursts)

    assert set(result.keys()) == {
        "platform_counts",
        "track_counts",
    }
