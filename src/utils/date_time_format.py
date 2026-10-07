from datetime import datetime


def to_iso_utc(date_str: str) -> str:
    dt = datetime.strptime(
        f"{date_str} +0000",
        "%Y-%m-%d %H:%M:%S %z",
    )

    return dt.isoformat()