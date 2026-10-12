from collections.abc import Sequence
from logging import Logger

from ingestion.annotation import AnnotationBurst
from utils.date_time_format import to_iso_utc


class MetadataValidator:
    def __init__(
        self,
        bursts_by_product: dict,
        config: dict,
        logger: Logger,
    ):
        self.bursts_by_product = bursts_by_product

        self.logger = logger

        self.platform = config["SAR_PARAMETERS"]["PLATFORM"]
        self.orbit_direction = config["SAR_PARAMETERS"]["ORBIT_DIRECTION"]
        self.relative_orbit = config["SAR_PARAMETERS"]["RELATIVE_ORBIT"]
        self.swath = config["SAR_PARAMETERS"]["SWATH"]

        self.polarisation = config["COPERNICUS_PRODUCT"]["POLARIZATION"]

    def inspect_burst_identity(self) -> dict:
        """
        Inspect the burst identity for each product.

        Returns:
            dict: Dictionary mapping product IDs to lists of burst identity information.
        """
        burst_identity_by_product = {}

        self._require_nonempty_bursts_by_product()

        for product_id, product_bursts in self.bursts_by_product.items():
            burst_identity_by_product[product_id] = [
                {
                    "burst_id": burst["BurstId"],
                    "absolute_burst_id": burst["AbsoluteBurstId"],
                    "swath": burst["SwathIdentifier"],
                }
                for burst in product_bursts
            ]

        return burst_identity_by_product

    def validate_burst_identity(self) -> dict:
        """
        Compare burst-number sets across parent SLC products.

        Returns:
            dict: Dictionary containing consistency information for each product and overall consistency.
        """

        self._require_nonempty_bursts_by_product()

        reference_product_id = next(iter(self.bursts_by_product))

        reference_bursts = self.bursts_by_product[reference_product_id]

        reference_ids = {burst["BurstId"] for burst in reference_bursts}

        results = {}

        for product_id, product_bursts in self.bursts_by_product.items():
            burst_ids = [burst["BurstId"] for burst in product_bursts]
            current_ids = set(burst_ids)

            missing = reference_ids - current_ids
            unexpected = current_ids - reference_ids
            duplicates = len(burst_ids) != len(current_ids)

            results[product_id] = {
                "burst_ids": sorted(current_ids),
                "missing": sorted(missing),
                "unexpected": sorted(unexpected),
                "has_duplicates": duplicates,
                "is_consistent": (not missing and not unexpected and not duplicates),
            }

        return {
            "reference_product_id": reference_product_id,
            "reference_burst_ids": sorted(reference_ids),
            "products": results,
            "all_consistent": all(
                result["is_consistent"] for result in results.values()
            ),
        }

    def validate_acquisition_metadata(self) -> list[dict]:
        """
        Validate acquisition metadata against expected values.

        Returns:
            list[dict]: List of mismatched metadata entries.
        """
        expected_metadata = {
            "PlatformSerialIdentifier": self.platform,
            "OrbitDirection": self.orbit_direction,
            "RelativeOrbitNumber": self.relative_orbit,
            "SwathIdentifier": self.swath,
            "PolarisationChannels": self.polarisation,
        }

        mismatches = []

        self._require_nonempty_bursts_by_product()

        for product_id, product_bursts in self.bursts_by_product.items():
            for burst in product_bursts:
                for field, expected_value in expected_metadata.items():
                    actual_value = burst.get(field)

                    if actual_value != expected_value:
                        mismatches.append(
                            {
                                "product_id": product_id,
                                "burst_id": burst.get("BurstId"),
                                "field": field,
                                "expected": expected_value,
                                "actual": actual_value,
                            }
                        )

        return mismatches

    def inspect_orbit_metadata(self) -> dict:
        """Inspect available orbit-related fields in selected bursts."""

        self._require_nonempty_bursts_by_product()

        first_product_id = next(iter(self.bursts_by_product))
        first_burst = self.bursts_by_product[first_product_id][0]

        orbit_related_fields = {
            key: value
            for key, value in first_burst.items()
            if any(
                keyword in key.lower()
                for keyword in ("orbit", "position", "velocity", "statevector")
            )
        }

        return {
            "product_id": first_product_id,
            "orbit_fields": orbit_related_fields,
            "available_fields": sorted(first_burst.keys()),
        }

    def map_catalogue_bursts_to_annotation(
        self,
        catalogue_bursts: list[dict],
        annotation_bursts: Sequence[AnnotationBurst],
    ) -> dict[int, int]:
        annotation_by_time = {}

        for index, burst in enumerate(annotation_bursts):
            azimuth_time = to_iso_utc(burst["azimuth_time"])

            if azimuth_time in annotation_by_time:
                raise ValueError(
                    f"Duplicate annotation azimuth timestamp: {azimuth_time}"
                )

            annotation_by_time[azimuth_time] = index

        mapping = {}

        for burst in catalogue_bursts:
            burst_id = burst["BurstId"]
            azimuth_time = to_iso_utc(burst["AzimuthTime"])

            if burst_id in mapping:
                raise ValueError(f"Duplicate catalogue BurstId: {burst_id}")

            if azimuth_time not in annotation_by_time:
                raise ValueError(f"No annotation match for catalogue burst {burst_id}")

            mapping[burst_id] = annotation_by_time[azimuth_time]

        return mapping

    def _require_nonempty_bursts_by_product(self) -> None:

        if not self.bursts_by_product:
            raise ValueError("No acquisitions available for validation")
