from collections import Counter


def metadata_summary(
    bursts: list,
) -> dict[str, Counter]:
    """
    Catalogue exploration
    Summarize metadata for a list of bursts.

    Args:
        bursts (list): List of burst dictionaries.
        logger (Logger): Logger instance for logging.

    Returns:
        dict[str, Counter]: Dictionary containing platform and track counts.
    """

    platform_counts = Counter(burst["PlatformSerialIdentifier"] for burst in bursts)

    track_counts = Counter(
        (
            burst["OrbitDirection"],
            burst["RelativeOrbitNumber"],
            burst["SwathIdentifier"],
        )
        for burst in bursts
    )

    return {
        "platform_counts": platform_counts,
        "track_counts": track_counts,
    }
