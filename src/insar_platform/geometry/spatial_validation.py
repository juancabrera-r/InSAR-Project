from dataclasses import dataclass
from functools import reduce
from logging import Logger
from math import isfinite

from pyproj import Transformer
from shapely import transform, unary_union, wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry


@dataclass
class ValidateSpatialResult:
    common_footprint: object
    common_coverage_percentage: float
    acquisitions: dict


class ValidateSpatial:
    def __init__(
        self,
        logger: Logger,
        bursts_by_product: dict,
        aoi_wkt: str,
    ) -> None:
        self.logger = logger

        self.bursts_by_product = bursts_by_product
        self.aoi_wkt = aoi_wkt

    def quantify_coverage(self) -> ValidateSpatialResult:
        """
        Calculate per-acquisition and common AOI coverage.

        Returns:
            dict: Dictionary containing coverage information for each acquisition and the common coverage across all acquisitions.
        """

        self._require_nonempty_bursts_by_product()

        # 1. Load and validate AOI
        aoi_geometry = wkt.loads(self.aoi_wkt)
        self._validate_geometry(aoi_geometry, "AOI")

        if aoi_geometry.is_empty or not aoi_geometry.is_valid:
            raise ValueError("AOI geometry is invalid or empty")

        # 2. Transform WGS84 coordinates to ETRS89 / UTM zone 30N
        # TODO: Review if the CRS transformation is appropriate for all AOIs
        aoi_projected = self.project(aoi_geometry)
        aoi_area = aoi_projected.area

        if not isfinite(aoi_area) or aoi_area <= 0:
            raise ValueError("AOI projected area must be positive and finite")

        coverage_acquisition = {}
        covered_areas = []

        # 3. Calculate coverage for each parent SLC product
        for product_id, product_bursts in self.bursts_by_product.items():
            if not product_bursts:
                raise ValueError(f"No bursts available for product {product_id}")

            geometries = []

            for index, burst in enumerate(product_bursts):
                try:
                    geometry = shape(burst["GeoFootprint"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid GeoFootprint for product {product_id}, burst {index}"
                    ) from exc

                self._validate_geometry(
                    geometry, f"Product {product_id}, burst {index}"
                )
                geometries.append(geometry)

            acquisition_footprint = unary_union(geometries)

            self._validate_geometry(
                acquisition_footprint, f"Product {product_id} acquisition footprint"
            )

            footprint_projected = self.project(acquisition_footprint)

            covered_region = aoi_projected.intersection(footprint_projected)

            covered_area = covered_region.area

            if not isfinite(covered_area):
                raise ValueError(f"Non-finite coverage area for product {product_id}")

            coverage_percentage = 100.0 * covered_area / aoi_area

            covered_areas.append(covered_region)

            coverage_acquisition[product_id] = {
                "acquisition_footprint": acquisition_footprint,
                "coverage_percentage": coverage_percentage,
                "covered_region": covered_region,
            }

        common_footprint = reduce(
            lambda current, geometry: current.intersection(geometry),
            covered_areas,
        )

        common_coverage = 100.0 * common_footprint.area / aoi_area

        minimum_coverage = min(
            result["coverage_percentage"] for result in coverage_acquisition.values()
        )

        if not isfinite(common_coverage) or common_coverage > minimum_coverage + 1e-8:
            raise ValueError("Invalid common coverage across acquisitions")

        return ValidateSpatialResult(
            common_footprint=common_footprint,
            common_coverage_percentage=common_coverage,
            acquisitions=coverage_acquisition,
        )

    def _validate_geometry(
        self,
        geometry: BaseGeometry,
        name: str,
    ) -> None:
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"{name} is invalid or empty")

        if not isfinite(geometry.bounds[0]) or not all(
            isfinite(value) for value in geometry.bounds
        ):
            raise ValueError(f"{name} has non-finite bounds")

    def _require_nonempty_bursts_by_product(self) -> None:
        if not self.bursts_by_product:
            raise ValueError("No acquisitions available for validation")

    def project(self, geometry: BaseGeometry) -> BaseGeometry:

        transformer = Transformer.from_crs(
            "EPSG:4326",
            "EPSG:25830",
            always_xy=True,
        )

        projected = transform(
            geometry,
            transformer.transform,  # type: ignore
            interleaved=False,
        )  # pyright: ignore[reportCallIssue]
        self._validate_geometry(projected, "Projected geometry")

        return projected
