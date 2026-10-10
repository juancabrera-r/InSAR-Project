import hashlib
import logging
from collections import Counter, defaultdict

import requests

from utils.date_time_format import to_iso_utc


class Sentinel1Ingestion:
    def __init__(self, config: dict, aoi_wkt: str):

        self.initial_url = config["URL"]

        self.start_date_iso = to_iso_utc(config["START_DATE"])
        self.end_date_iso = to_iso_utc(config["END_DATE"])
        self.area = config["COPERNICUS_PRODUCT"]["AREA"]
        self.parent_product_type = config["COPERNICUS_PRODUCT"]["PARENTPRODUCTTYPE"]
        self.polarization = config["COPERNICUS_PRODUCT"]["POLARIZATION"]

        self.platform = config["SAR_PARAMETERS"]["PLATFORM"]
        self.orbit_direction = config["SAR_PARAMETERS"]["ORBIT_DIRECTION"]
        self.relative_orbit = config["SAR_PARAMETERS"]["RELATIVE_ORBIT"]
        self.swath = config["SAR_PARAMETERS"]["SWATH"]

        self.aoi_wkt = aoi_wkt

        self.original_fingerprint = config["BURSTS_FINGERPRINT"]

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

        self.logger.debug(
            f"Searching acquisitions from {self.start_date_iso} to {self.end_date_iso} \n"
            f"for polygons: {self.aoi_wkt} \n"
            f"area: {self.area} \n"
            f"parent product type: {self.parent_product_type} \n"
            f"polarization: {self.polarization}"
        )

        filter_expression = (
            f"ContentDate/Start ge {self.start_date_iso} "
            f"and ContentDate/Start lt {self.end_date_iso} "
            f"and OData.CSC.Intersects("
            f"area={self.area}'SRID=4326;{self.aoi_wkt}') "
            f"and ParentProductType eq '{self.parent_product_type}' "
            f"and PolarisationChannels eq '{self.polarization}'"
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

    def get_products_by_group(self, bursts: list) -> dict[tuple, set]:

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

        return products_by_group

    def get_bursts_dates(self, bursts: list) -> list[str]:

        self._require_nonempty_bursts(bursts)

        selected_bursts = [
            burst
            for burst in bursts
            if burst["PlatformSerialIdentifier"] == self.platform
            and burst["OrbitDirection"] == self.orbit_direction
            and burst["RelativeOrbitNumber"] == self.relative_orbit
            and burst["SwathIdentifier"] == self.swath
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

    def get_deduplicated_id(self, bursts: list) -> dict[str, int]:

        self._require_nonempty_bursts(bursts)

        id_counts = Counter(burst["Id"] for burst in bursts)

        duplicate_ids = {
            burst_id: count for burst_id, count in id_counts.items() if count > 1
        }

        if not duplicate_ids:
            self.logger.debug("No duplicate burst IDs found")
            return {}

        return duplicate_ids

    def get_bursts_by_product(self, bursts: list) -> dict:

        bursts_by_product = defaultdict(list)

        for burst in bursts:
            if (
                burst["PlatformSerialIdentifier"] == self.platform
                and burst["OrbitDirection"] == self.orbit_direction
                and burst["RelativeOrbitNumber"] == self.relative_orbit
                and burst["SwathIdentifier"] == self.swath
            ):
                bursts_by_product[burst["ParentProductId"]].append(burst)

        return dict(bursts_by_product)

    def _bursts_fingerprint(self, bursts: list) -> None:

        self._require_nonempty_bursts(bursts)

        burst_ids = sorted(burst["Id"] for burst in bursts)

        fingerprint = hashlib.sha256("\n".join(burst_ids).encode("utf-8")).hexdigest()

        self.logger.debug("New fingerprint: %s", fingerprint)

        if fingerprint == self.original_fingerprint:
            self.logger.debug("Fingerprint matches the original")
        else:
            self.logger.warning("Fingerprint does not match the original")
