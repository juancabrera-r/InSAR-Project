from logging import Logger


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

    def _require_nonempty_bursts_by_product(self) -> None:

        if not self.bursts_by_product:
            raise ValueError("No acquisitions available for validation")
