import logging

import requests

from utils.date_time_format import to_iso_utc


class Sentinel1Ingestion:
    def __init__(self, config: dict, aoi_wkt: str):
        self.config = config

        self.start_date = config["START_DATE"]
        self.end_date = config["END_DATE"]
        self.url = config["URL"]

        self.aoi_wkt = aoi_wkt

        self.logger = logging.getLogger(__name__)

    def search_acquisitions(self) -> list[dict]:
        start_date_iso = to_iso_utc(self.start_date)
        end_date_iso = to_iso_utc(self.end_date)

        self.logger.debug(
            f"Searching acquisitions from {start_date_iso} to {end_date_iso} "
            f"for polygons: {self.aoi_wkt}"
        )

        filter_expression = (
            f"ContentDate/Start ge {start_date_iso} "
            f"and ContentDate/End le {end_date_iso} "
            f"and OData.CSC.Intersects("
            f"area=geography'SRID=4326;{self.aoi_wkt}') "
            f"and ParentProductType eq 'IW_SLC__1S' "
            f"and PolarisationChannels eq 'VV'"
        )

        response = requests.get(
            self.url,
            params={"$filter": filter_expression},
            timeout=30,
        )

        response.raise_for_status()

        payload = response.json()

        result = payload.get("value")
        if not isinstance(result, list):
            raise TypeError("Invalid response: expected a list of acquisitions")

        return result
