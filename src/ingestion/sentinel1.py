import hashlib
import logging
from collections import Counter, defaultdict

import requests

from utils.date_time_format import to_iso_utc


class Sentinel1Ingestion:
    def __init__(self, config: dict, aoi_wkt: str):
        self.config = config

        self.start_date = config["START_DATE"]
        self.end_date = config["END_DATE"]
        self.initial_url = config["URL"]

        self.aoi_wkt = aoi_wkt

        self.logger = logging.getLogger(__name__)

    def request_first_page(self, url: str, filter_expression: str) -> dict:

        response = requests.get(
            url,
            params={
                "$filter": filter_expression,
                "$orderby": "Id asc",
            },
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    def request_next_page(self, url: str) -> dict:

        response = requests.get(
            url,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    def search_bursts(self) -> list[dict]:

        start_date_iso = to_iso_utc(self.start_date)
        end_date_iso = to_iso_utc(self.end_date)
        area = self.config["COPERNICUS_PRODUCT"]["AREA"]
        parent_product_type = self.config["COPERNICUS_PRODUCT"]["PARENTPRODUCTTYPE"]
        polarization = self.config["COPERNICUS_PRODUCT"]["POLARIZATION"]

        self.logger.debug(
            f"Searching acquisitions from {start_date_iso} to {end_date_iso} \n"
            f"for polygons: {self.aoi_wkt} \n"
            f"area: {area} \n"
            f"parent product type: {parent_product_type} \n"
            f"polarization: {polarization}"
        )

        filter_expression = (
            f"ContentDate/Start ge {start_date_iso} "
            f"and ContentDate/Start lt {end_date_iso} "
            f"and OData.CSC.Intersects("
            f"area={area}'SRID=4326;{self.aoi_wkt}') "
            f"and ParentProductType eq '{parent_product_type}' "
            f"and PolarisationChannels eq '{polarization}'"
        )

        all_bursts = []

        payload = self.request_first_page(
            self.initial_url,
            filter_expression,
        )

        result = payload.get("value")

        if not isinstance(result, list):
            raise TypeError("Invalid response: expected a list of bursts")

        all_bursts.extend(result)

        # Get the next page from the first response.
        next_url = payload.get("@odata.nextLink")

        page_number = 1

        while next_url is not None:
            payload = self.request_next_page(next_url)
            page_number += 1

            result = payload.get("value")

            if not isinstance(result, list):
                raise TypeError("Invalid response: expected a list of bursts")

            self.logger.debug(
                "Page %d: %d bursts",
                page_number,
                len(result),
            )

            all_bursts.extend(result)
            next_url = payload.get("@odata.nextLink")

        self._bursts_fingerprint(all_bursts)

        return all_bursts

    def _require_nonempty_bursts(self, bursts: list) -> None:

        if not bursts:
            raise ValueError("No bursts provided")

    def get_products_by_group(self, bursts: list) -> None:

        self._require_nonempty_bursts(bursts)

        products_by_group = defaultdict(set)

        for burst in bursts:
            key = (
                burst["PlatformSerialIdentifier"],
                burst["OrbitDirection"],
                burst["RelativeOrbitNumber"],
                burst["SwathIdentifier"],
            )
            products_by_group[key].add(burst["ParentProductId"])

        for group, products in sorted(products_by_group.items()):
            self.logger.info("Group: %s, Unique SLC products: %d", group, len(products))

    def get_bursts_dates(self, bursts: list) -> list[str]:

        self._require_nonempty_bursts(bursts)

        platform = self.config["SAR_PARAMETERS"]["PLATFORM"]
        orbit_direction = self.config["SAR_PARAMETERS"]["ORBIT_DIRECTION"]
        relative_orbit = self.config["SAR_PARAMETERS"]["RELATIVE_ORBIT"]
        swath = self.config["SAR_PARAMETERS"]["SWATH"]

        selected_bursts = [
            burst
            for burst in bursts
            if burst["PlatformSerialIdentifier"] == platform
            and burst["OrbitDirection"] == orbit_direction
            and burst["RelativeOrbitNumber"] == relative_orbit
            and burst["SwathIdentifier"] == swath
        ]

        dates = sorted(
            {burst["ContentDate"]["Start"][:10] for burst in selected_bursts}
        )

        return dates

    def get_id(self, bursts: list) -> dict[str, int]:

        self._require_nonempty_bursts(bursts)

        ids = [burst["Id"] for burst in bursts]
        unique_count = len(set(ids))

        return {
            "total": len(ids),
            "unique": unique_count,
            "duplicates": len(ids) - unique_count,
        }

    def get_deduplicated_id(self, bursts: list) -> None:

        self._require_nonempty_bursts(bursts)

        id_counts = Counter(burst["Id"] for burst in bursts)

        duplicate_ids = {
            burst_id: count for burst_id, count in id_counts.items() if count > 1
        }

        if not duplicate_ids:
            self.logger.info("No duplicate burst IDs found")
            return

        self.logger.info(
            "Duplicate burst IDs: %s",
            duplicate_ids,
        )

    def _bursts_fingerprint(self, bursts: list) -> None:

        self._require_nonempty_bursts(bursts)

        original_fingerprint = self.config["BURSTS_FINGERPRINT"]
        burst_ids = sorted(burst["Id"] for burst in bursts)

        fingerprint = hashlib.sha256("\n".join(burst_ids).encode("utf-8")).hexdigest()

        self.logger.info("New fingerprint: %s", fingerprint)

        if fingerprint == original_fingerprint:
            self.logger.info("Fingerprint matches the original")
        else:
            self.logger.warning("Fingerprint does not match the original")
