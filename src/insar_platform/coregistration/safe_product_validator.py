"""Structural validation of Sentinel-1 SAFE products."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


class SafeValidationError(ValueError):
    """Raised when a Sentinel-1 SAFE product is invalid."""


@dataclass(frozen=True)
class SafeValidationResult:
    product_path: Path
    product_name: str
    is_zip: bool
    annotation_count: int
    measurement_count: int


class SafeProductValidator:
    """Validate basic Sentinel-1 SAFE product structure."""

    def validate(self, product_path: str | Path) -> SafeValidationResult:
        path = Path(product_path).expanduser().resolve()

        if path.is_dir():
            return self._validate_directory(path)

        if path.is_file() and path.suffix.lower() == ".zip":
            return self._validate_zip(path)

        raise SafeValidationError(f"Expected a SAFE directory or ZIP archive: {path}")

    def _validate_directory(self, path: Path) -> SafeValidationResult:
        if not path.name.endswith(".SAFE"):
            raise SafeValidationError(f"Directory does not have .SAFE suffix: {path}")

        manifest = path / "manifest.safe"

        if not manifest.is_file():
            raise SafeValidationError("Missing manifest.safe")

        self._validate_xml(manifest.read_bytes())

        annotations = list((path / "annotation").glob("*.xml"))
        measurements = list((path / "measurement").glob("*.tiff"))

        if not annotations:
            raise SafeValidationError("No annotation XML files found")

        if not measurements:
            raise SafeValidationError("No measurement TIFF files found")

        return SafeValidationResult(
            product_path=path,
            product_name=path.name,
            is_zip=False,
            annotation_count=len(annotations),
            measurement_count=len(measurements),
        )

    def _validate_zip(self, path: Path) -> SafeValidationResult:
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()

                manifests = [
                    name for name in names if name.endswith(".SAFE/manifest.safe")
                ]

                if len(manifests) != 1:
                    raise SafeValidationError("Expected exactly one SAFE manifest")

                root = manifests[0].removesuffix("manifest.safe")

                annotations = [
                    name
                    for name in names
                    if name.startswith(root + "annotation/") and name.endswith(".xml")
                ]

                measurements = [
                    name
                    for name in names
                    if name.startswith(root + "measurement/")
                    and name.lower().endswith((".tiff", ".tif"))
                ]

                if not annotations or not measurements:
                    raise SafeValidationError("Missing annotation or measurement files")

                self._validate_xml(archive.read(manifests[0]))

                return SafeValidationResult(
                    product_path=path,
                    product_name=root.rstrip("/"),
                    is_zip=True,
                    annotation_count=len(annotations),
                    measurement_count=len(measurements),
                )

        except (zipfile.BadZipFile, OSError) as exc:
            raise SafeValidationError(f"Unable to read SAFE ZIP: {path}") from exc

    @staticmethod
    def _validate_xml(content: bytes) -> None:
        try:
            ET.fromstring(content)
        except ET.ParseError as exc:
            raise SafeValidationError("Invalid SAFE manifest XML") from exc
