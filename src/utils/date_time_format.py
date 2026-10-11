from datetime import UTC, datetime


def to_iso_utc(date_str: str) -> str:
    dt = datetime.fromisoformat(date_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC).isoformat()
