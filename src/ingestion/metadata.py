from collections import Counter
from logging import Logger


def metadata_summary(bursts: list, logger: Logger) -> None:

    platform_counts = Counter(burst["PlatformSerialIdentifier"] for burst in bursts)

    track_counts = Counter(
        (
            burst["OrbitDirection"],
            burst["RelativeOrbitNumber"],
            burst["SwathIdentifier"],
        )
        for burst in bursts
    )

    logger.info("Platforms: %s", platform_counts)
    logger.info("Tracks: %s", track_counts)
