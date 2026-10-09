import json
from pathlib import Path


class GeojsonManager:
    def __init__(self, geojson: Path):
        self.geojson = geojson

    def read_geojson(self) -> dict:
        with self.geojson.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if data.get("type") != "Polygon":
            raise ValueError(
                f"Invalid AOI GeoJSON: expected Polygon, found {data.get('type')!r}"
            )

        return data

    def polygon_to_wkt(self) -> str:
        polygon = self.read_geojson()
        rings = []

        for ring in polygon["coordinates"]:
            if ring[0] != ring[-1]:
                raise ValueError("Polygon ring must be closed")

            coordinates = ", ".join(f"{lon} {lat}" for lon, lat in ring)

            rings.append(f"({coordinates})")

        return f"POLYGON ({', '.join(rings)})"
