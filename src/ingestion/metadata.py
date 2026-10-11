from collections import Counter
from datetime import datetime


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


def select_earliest_acquisitions(
    bursts_by_product: dict,
    count: int = 2,
) -> list[dict]:
    """
    Select the earliest acquisitions from each product based on acquisition time.

    Args:
        bursts_by_product (dict): Dictionary of bursts grouped by product ID.
        count (int, optional): Number of earliest acquisitions to select. Defaults to 2.

    Returns:
        list[dict]: List of dictionaries containing acquisition information.
    """

    if count < 1:
        raise ValueError("count must be at least 1")

    acquisitions = []

    for product_id, product_bursts in bursts_by_product.items():
        if not product_bursts:
            raise ValueError(f"Product {product_id} has no bursts")

        first_burst = product_bursts[0]

        acquisition = {
            "product_id": product_id,
            "product_name": first_burst["ParentProductName"],
            "acquisition_time": first_burst["ContentDate"]["Start"],
        }

        acquisitions.append(acquisition)

    acquisitions.sort(
        key=lambda acquisition: datetime.fromisoformat(acquisition["acquisition_time"])
    )

    return acquisitions[:count]
