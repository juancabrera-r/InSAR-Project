import json
import logging
from pathlib import Path


class GeojsonManager:
    def __init__(self, geojson: Path):
        self.geojson = geojson
        self.logger = logging.getLogger(__name__)

    def read_geojson(self) -> dict:
        with self.geojson.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if data.get("type") != "FeatureCollection":
            raise ValueError("Invalid GeoJSON: expected a FeatureCollection")

        return data

    def get_polygon(self) -> dict:
        data = self.read_geojson()
        features = data.get("features", [])

        if len(features) != 1:
            raise ValueError(f"Expected exactly one feature, found {len(features)}")

        geometry = features[0].get("geometry")

        if geometry is None:
            raise ValueError("Feature has no geometry")

        if geometry.get("type") != "Polygon":
            raise ValueError(f"Expected Polygon geometry, found {geometry.get('type')}")

        return geometry

    def polygon_to_wkt(self) -> str:
        polygon = self.get_polygon()
        rings = []

        for ring in polygon["coordinates"]:
            if ring[0] != ring[-1]:
                raise ValueError("Polygon ring must be closed")

            coordinates = ", ".join(f"{lon} {lat}" for lon, lat in ring)

            rings.append(f"({coordinates})")

        return f"POLYGON ({', '.join(rings)})"
