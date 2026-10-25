import shutil
from datetime import UTC, datetime, timedelta
from logging import Logger
from pathlib import Path
from zipfile import ZipFile, is_zipfile

import requests


class CDSEIngestion:
    def __init__(
        self,
        config: dict,
        selected_pair: list,
        env: dict,
        logger: Logger,
    ):

        self.config = config
        self.selected_pair = selected_pair

        self.initial_url = config["URL"]
        self.cdse_username = env["CDSE_USERNAME"]
        self.cdse_password = env["CDSE_PASSWORD"]

        self.logger = logger

    def authenticate(self) -> str:
        """Authenticate with CDSE and return an access token."""

        token_url = (
            "https://identity.dataspace.copernicus.eu/"
            "auth/realms/CDSE/protocol/openid-connect/token"
        )

        payload = {
            "username": self.cdse_username,
            "password": self.cdse_password,
            "grant_type": "password",
            "client_id": "cdse-public",
        }

        response = requests.post(
            token_url,
            data=payload,
            timeout=30,
        )

        response.raise_for_status()

        token_data = response.json()

        return token_data["access_token"]

    def get_product_metadata(self, product_id: str) -> dict:
        """Retrieve the metadata for a given product."""

        url = self.initial_url + f"/odata/v1/Products({product_id})"

        response = requests.get(url, timeout=30)
        response.raise_for_status()

        return response.json()

    def get_product_nodes(self, product_id: str) -> dict:
        """Retrieve the nodes for a given product."""

        url = self.initial_url + f"/odata/v1/Products({product_id})/Nodes"

        response = requests.get(url, timeout=30)
        response.raise_for_status()

        return response.json()

    def get_children_node(
        self,
        product_nodes: dict,
    ) -> dict:
        """Retrieve the children node for a given product's root node."""

        root_node = product_nodes["result"][0]
        children_url = root_node["Nodes"]["uri"]

        response = requests.get(children_url, timeout=30)
        response.raise_for_status()

        children = response.json()

        return children

    def get_directory_files(
        self,
        children_node: dict,
        directory_name: str,
    ) -> dict:
        """Retrieve the files for a given directory node."""

        directory_node = next(
            (
                node
                for node in children_node["result"]
                if node["Name"] == directory_name
            ),
            None,
        )

        if directory_node is None:
            raise ValueError(f"Directory {directory_name!r} not found in SAFE product")

        directory_url = directory_node["Nodes"]["uri"]

        response = requests.get(directory_url, timeout=30)
        response.raise_for_status()

        return response.json()

    def download_annotation_file(
        self,
        annotation_node: dict,
        destination_path: Path,
    ) -> Path:
        """Download or reuse an annotation XML file."""

        return self.download_node_file(
            node=annotation_node,
            destination_path=destination_path,
        )

    def download_measurement_file(
        self,
        measurement_node: dict,
        destination_path: Path,
    ) -> Path:
        """Download or reuse a measurement TIFF file."""

        return self.download_node_file(
            node=measurement_node,
            destination_path=destination_path,
        )

    def search_orbit_products(
        self,
        satellite: str,
        burst_start: datetime,
        burst_end: datetime,
        orbit_type: str = "AUX_POEORB",
    ) -> list[dict]:
        """Find orbit products whose catalogue validity covers the burst."""

        if orbit_type not in {"AUX_POEORB", "AUX_RESORB"}:
            raise ValueError(f"Unsupported orbit type: {orbit_type}")

        if satellite not in {"S1A", "S1B", "S1C", "S1D"}:
            raise ValueError(f"Unsupported satellite: {satellite}")

        if (
            burst_start.tzinfo is None
            or burst_end.tzinfo is None
            or burst_start > burst_end
        ):
            raise ValueError("Invalid burst interval")

        burst_start = burst_start.astimezone(UTC)
        burst_end = burst_end.astimezone(UTC)

        # A bounded discovery window; final validity is checked
        # against the downloaded EOF metadata.
        search_start = burst_start - timedelta(days=3)
        search_end = burst_end + timedelta(days=3)

        start_iso = search_start.isoformat().replace("+00:00", "Z")
        end_iso = search_end.isoformat().replace("+00:00", "Z")

        filters = [
            "Collection/Name eq 'SENTINEL-1'",
            f"ContentDate/Start le {end_iso}",
            f"ContentDate/End ge {start_iso}",
            (
                "Attributes/OData.CSC.StringAttribute/any(att:"
                "att/Name eq 'productType' and "
                f"att/OData.CSC.StringAttribute/Value eq '{orbit_type}')"
            ),
        ]

        url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

        products = []
        params = {
            "$filter": " and ".join(filters),
            "$top": 100,
        }

        while url:
            response = requests.get(
                url,
                params=params,
                timeout=(30, 60),
            )
            response.raise_for_status()

            payload = response.json()
            products.extend(payload.get("value", []))

            url = payload.get("@odata.nextLink")
            params = None

        return [
            product
            for product in products
            if product.get("Name", "").startswith(satellite)
            and orbit_type in product.get("Name", "")
        ]

    def download_orbit_product(
        self,
        product: dict,
        destination_directory: Path,
    ) -> Path:
        """Download an orbit product and extract its EOF file."""

        destination_directory.mkdir(parents=True, exist_ok=True)

        product_id = product["Id"]
        product_name = product["Name"]

        eof_path = destination_directory / (
            product_name.removesuffix(".zip").removesuffix(".ZIP")
        )

        if eof_path.suffix.upper() != ".EOF":
            eof_path = eof_path.with_suffix(".EOF")

        if eof_path.is_file() and eof_path.stat().st_size > 0:
            return eof_path

        download_url = (
            "https://zipper.dataspace.copernicus.eu/"
            f"odata/v1/Products({product_id})/$value"
        )

        temporary_path = destination_directory / f"{product_id}.download"

        access_token = self.authenticate()

        try:
            with requests.get(
                download_url,
                headers={"Authorization": f"Bearer {access_token}"},
                stream=True,
                timeout=(30, 180),
            ) as response:
                response.raise_for_status()

                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)

            if is_zipfile(temporary_path):
                with ZipFile(temporary_path) as archive:
                    eof_members = [
                        member
                        for member in archive.infolist()
                        if not member.is_dir()
                        and member.filename.lower().endswith(".eof")
                    ]

                    if len(eof_members) != 1:
                        raise ValueError(
                            f"Expected one EOF file, found {len(eof_members)}"
                        )

                    with (
                        archive.open(eof_members[0]) as source,
                        eof_path.open("wb") as destination,
                    ):
                        shutil.copyfileobj(source, destination)
            else:
                # Some product endpoints may deliver EOF content directly.
                with temporary_path.open("rb") as source:
                    signature = source.read(256).lstrip()

                if not signature.startswith((b"<?xml", b"<Earth_Explorer_File")):
                    raise ValueError(
                        f"Unexpected orbit download format: {product_name}"
                    )

                temporary_path.replace(eof_path)

            return eof_path

        except Exception:
            eof_path.unlink(missing_ok=True)
            raise

        finally:
            temporary_path.unlink(missing_ok=True)

    def download_orbit_for_acquisition(
        self,
        satellite: str,
        burst_start: datetime,
        burst_end: datetime,
        destination_directory: Path,
    ) -> Path | None:

        from insar_platform.geometry.orbit_selection import select_external_orbit

        existing = select_external_orbit(
            orbit_directory=destination_directory,
            satellite=satellite,
            burst_start=burst_start,
            burst_end=burst_end,
        )

        if existing is not None:
            return existing.file_path

        for orbit_type in ("AUX_POEORB", "AUX_RESORB"):
            products = self.search_orbit_products(
                satellite=satellite,
                burst_start=burst_start,
                burst_end=burst_end,
                orbit_type=orbit_type,
            )

            for product in products:
                self.logger.info(
                    "Downloading %s orbit product: %s",
                    orbit_type,
                    product["Name"],
                )

                self.download_orbit_product(
                    product=product,
                    destination_directory=destination_directory,
                )

                selected = select_external_orbit(
                    orbit_directory=destination_directory,
                    satellite=satellite,
                    burst_start=burst_start,
                    burst_end=burst_end,
                )

                if selected is not None:
                    return selected.file_path

        return None

    def download_manifest_file(
        self,
        children_node: dict,
        destination_path: Path,
    ) -> Path:
        """Download or reuse the original SAFE manifest."""

        from xml.etree import ElementTree as ET

        manifest_node = next(
            (
                node
                for node in children_node["result"]
                if node["Name"] == "manifest.safe"
            ),
            None,
        )

        if manifest_node is None:
            raise ValueError("manifest.safe not found in CDSE product")

        manifest_path = self.download_node_file(
            node=manifest_node,
            destination_path=destination_path,
        )

        # Validate both newly downloaded and cached manifests.
        ET.parse(manifest_path)

        return manifest_path

    def download_node_file(
        self,
        node: dict,
        destination_path: Path,
    ) -> Path:
        """Download a CDSE file only if it is not already cached."""

        destination_path = Path(destination_path)

        # Reuse an existing nonempty file.
        if destination_path.is_file() and destination_path.stat().st_size > 0:
            self.logger.info(
                "Using cached CDSE file: %s",
                destination_path,
            )
            return destination_path

        node_uri = node["Nodes"]["uri"]

        if not node_uri.endswith("/Nodes"):
            raise ValueError("Unexpected CDSE node URI structure")

        file_url = node_uri.removesuffix("/Nodes") + "/$value"

        destination_path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path = destination_path.with_name(destination_path.name + ".part")

        access_token = self.authenticate()

        try:
            with requests.get(
                file_url,
                headers={"Authorization": f"Bearer {access_token}"},
                stream=True,
                timeout=(30, 120),
            ) as response:
                response.raise_for_status()

                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)

            if temporary_path.stat().st_size == 0:
                raise ValueError(f"Downloaded CDSE file is empty: {destination_path}")

            temporary_path.replace(destination_path)

        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

        self.logger.info(
            "Downloaded CDSE file: %s",
            destination_path,
        )

        return destination_path
