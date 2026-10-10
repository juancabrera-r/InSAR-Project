import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform


def export_processing_aoi(
    common_footprint,
    output_path: Path,
) -> None:
    """Export the common acquisition footprint as WGS84 GeoJSON."""

    if common_footprint.is_empty or not common_footprint.is_valid:
        raise ValueError("Common footprint is empty or invalid")

    transformer = Transformer.from_crs(
        "EPSG:25830",
        "EPSG:4326",
        always_xy=True,
    )

    footprint_wgs84 = transform(
        transformer.transform,
        common_footprint,
    )

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": "Alto Guadalentin processing AOI",
                },
                "geometry": mapping(footprint_wgs84),
            }
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(geojson, file, indent=2)
