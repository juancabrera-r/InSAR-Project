"""Inspect file dependencies declared by Sentinel-1 SAFE manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class SafeFileReference:
    """A file referenced by the original SAFE manifest."""

    file_id: str
    relative_path: Path
    category: str


@dataclass(frozen=True)
class SafeManifestReport:
    """File references and local availability for one acquisition."""

    manifest_path: Path
    references: tuple[SafeFileReference, ...]
    available: tuple[SafeFileReference, ...]
    missing: tuple[SafeFileReference, ...]


class SafeManifestInspector:
    """Inspect SAFE file references without modifying source files."""

    @staticmethod
    def _parse_reference(file_id: str, href: str) -> SafeFileReference:
        decoded = unquote(href)
        parsed = urlsplit(decoded)

        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError(f"Unsupported SAFE file reference: {href}")

        normalized = parsed.path.replace("\\", "/")
        parts = normalized.removeprefix("./").split("/")

        if (
            not parts
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
            or normalized.startswith("/")
        ):
            raise ValueError(f"Unsafe SAFE file reference: {href}")

        relative_path = Path(*parts)
        category = parts[0] if len(parts) > 1 else "root"

        return SafeFileReference(
            file_id=file_id,
            relative_path=relative_path,
            category=category,
        )

    def inspect(
        self,
        manifest_path: str | Path,
        annotation_directory: str | Path,
        measurement_directory: str | Path,
    ) -> SafeManifestReport:
        """Compare manifest references with locally downloaded files."""

        manifest = Path(manifest_path).resolve()
        annotation_dir = Path(annotation_directory).resolve()
        measurement_dir = Path(measurement_directory).resolve()

        if not manifest.is_file():
            raise FileNotFoundError(f"Manifest not found: {manifest}")

        root = ET.parse(manifest).getroot()

        references: list[SafeFileReference] = []
        available: list[SafeFileReference] = []
        missing: list[SafeFileReference] = []

        for data_object in root.findall(".//{*}dataObject"):
            file_id = data_object.get("ID", "")

            for file_location in data_object.findall(".//{*}fileLocation"):
                href = file_location.get("href")

                if not href:
                    raise ValueError(f"Missing fileLocation href for {file_id}")

                reference = self._parse_reference(file_id, href)
                references.append(reference)

                parts = reference.relative_path.parts

                if reference.category == "annotation":
                    local_path = annotation_dir.joinpath(*parts[1:])
                elif reference.category == "measurement":
                    local_path = measurement_dir.joinpath(*parts[1:])
                elif reference.relative_path.name == "manifest.safe":
                    local_path = manifest
                else:
                    missing.append(reference)
                    continue

                if local_path.is_file() and local_path.stat().st_size > 0:
                    available.append(reference)
                else:
                    missing.append(reference)

        if not references:
            raise ValueError(f"No file references found in SAFE manifest: {manifest}")

        return SafeManifestReport(
            manifest_path=manifest,
            references=tuple(references),
            available=tuple(available),
            missing=tuple(missing),
        )
