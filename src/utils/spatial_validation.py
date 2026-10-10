from functools import reduce
from logging import Logger

from pyproj import Transformer
from shapely import unary_union, wkt
from shapely.geometry import shape
from shapely.ops import transform


class ValidateSpatial:
    def __init__(
        self,
        logger: Logger,
        bursts_by_product: dict,
        aoi_wkt: str,
    ):
        self.logger = logger

        self.bursts_by_product = bursts_by_product
        self.aoi_wkt = aoi_wkt

    def quantify_coverage(self) -> dict:
        """
        Calculate per-acquisition and common AOI coverage.

        Returns:
            dict: Dictionary containing coverage information for each acquisition and the common coverage across all acquisitions.
        """

        self._require_nonempty_bursts_by_product()

        # 1. Load and validate AOI
        aoi_geometry = wkt.loads(self.aoi_wkt)

        if aoi_geometry.is_empty or not aoi_geometry.is_valid:
            raise ValueError("AOI geometry is invalid or empty")

        # 2. Transform WGS84 coordinates to ETRS89 / UTM zone 30N
        transformer = Transformer.from_crs(
            "EPSG:4326",
            "EPSG:25830",
            always_xy=True,
        )

        aoi_projected = transform(
            transformer.transform,
            aoi_geometry,
        )

        aoi_area = aoi_projected.area

        if aoi_area <= 0:
            raise ValueError("AOI projected area must be greater than zero")

        coverage_acquisition = {}
        covered_areas = []

        # 3. Calculate coverage for each parent SLC product
        for product_id, product_bursts in self.bursts_by_product.items():
            geometries = [shape(burst["GeoFootprint"]) for burst in product_bursts]

            if not geometries or not all(
                geometry.is_valid and not geometry.is_empty for geometry in geometries
            ):
                raise ValueError(
                    f"Invalid or missing burst geometries for product {product_id}"
                )

            acquisition_footprint = unary_union(geometries)

            footprint_projected = transform(
                transformer.transform,
                acquisition_footprint,
            )

            covered_region = aoi_projected.intersection(footprint_projected)

            coverage_percentage = (covered_region.area / aoi_area) * 100

            covered_areas.append(covered_region)

            coverage_acquisition[product_id] = {
                "acquisition_footprint": acquisition_footprint,
                "coverage_percentage": coverage_percentage,
                "covered_region": covered_region,
            }

            self.logger.info(
                "Product: %s | AOI coverage: %.2f%%",
                product_id,
                coverage_percentage,
            )

        # 4. Calculate common coverage across all acquisitions
        if not covered_areas:
            raise ValueError("No acquisition footprints available")

        common_footprint = reduce(
            lambda current, geometry: current.intersection(geometry),
            covered_areas,
        )

        common_coverage = (common_footprint.area / aoi_area) * 100

        # 5. Validate coverage invariants
        minimum_coverage = min(
            result["coverage_percentage"] for result in coverage_acquisition.values()
        )

        if common_coverage > minimum_coverage + 1e-8:
            raise ValueError(
                "Common coverage cannot exceed individual acquisition coverage"
            )

        # 6. Return results
        return {
            "acquisitions": coverage_acquisition,
            "common_footprint": common_footprint,
            "common_coverage_percentage": common_coverage,
        }

    def _require_nonempty_bursts_by_product(self) -> None:

        if not self.bursts_by_product:
            raise ValueError("No acquisitions available for validation")
