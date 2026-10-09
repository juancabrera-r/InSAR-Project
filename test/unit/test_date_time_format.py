import pytest

from utils.date_time_format import to_iso_utc


@pytest.mark.parametrize(
    ("input_date", "expected"),
    [
        (
            "2025-01-01 00:00:00",
            "2025-01-01T00:00:00+00:00",
        ),
        (
            "2025-06-15 12:30:45",
            "2025-06-15T12:30:45+00:00",
        ),
        (
            "2024-02-29 23:59:59",
            "2024-02-29T23:59:59+00:00",
        ),
    ],
)
def test_to_iso_utc_valid_dates(input_date, expected):
    result = to_iso_utc(input_date)

    assert result == expected


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2025-01-01",
        "2025-02-30 00:00:00",
        "2025-13-01 00:00:00",
        "2025-01-01 25:00:00",
        "2025-01-01T00:00:00Z",
        "invalid",
        "",
    ],
)
def test_to_iso_utc_invalid_dates(invalid_date):
    with pytest.raises(ValueError):
        to_iso_utc(invalid_date)


def test_to_iso_utc_preserves_time():
    result = to_iso_utc("2025-01-01 14:25:30")

    assert result == "2025-01-01T14:25:30+00:00"


def test_to_iso_utc_rejects_non_leap_year():
    with pytest.raises(ValueError):
        to_iso_utc("2025-02-29 00:00:00")