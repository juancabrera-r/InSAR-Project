from logging import Logger
from pathlib import Path

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

        url = self.initial_url + f"/odata/v1/Products({product_id})"

        response = requests.get(url, timeout=30)
        response.raise_for_status()

        return response.json()

    def get_product_nodes(self, product_id: str) -> dict:

        url = self.initial_url + f"/odata/v1/Products({product_id})/Nodes"

        response = requests.get(url, timeout=30)
        response.raise_for_status()

        return response.json()

    def get_children_node(
        self,
        product_nodes: dict,
    ) -> dict:

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
    ) -> None:

        node_uri = annotation_node["Nodes"]["uri"]

        if not node_uri.endswith("/Nodes"):
            raise ValueError("Unexpected CDSE node URI structure")

        file_url = node_uri.removesuffix("/Nodes") + "/$value"

        self.logger.debug("Annotation file URL: %s", file_url)

        access_token = self.authenticate()

        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        response = requests.get(
            file_url,
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()

        if not destination_path.parent.exists():
            destination_path.parent.mkdir(parents=True, exist_ok=True)

        with open(destination_path, "wb") as f:
            f.writelines(response.iter_content(chunk_size=8192))

    def download_measurement_file(
        self,
        measurement_node: dict,
        destination_path: Path,
    ) -> None:
        node_uri = measurement_node["Nodes"]["uri"]

        if not node_uri.endswith("/Nodes"):
            raise ValueError("Unexpected CDSE node URI structure")

        file_url = node_uri.removesuffix("/Nodes") + "/$value"

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.with_name(destination_path.name + ".part")

        if destination_path.is_file():
            return

        access_token = self.authenticate()

        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        try:
            with requests.get(
                file_url,
                headers=headers,
                stream=True,
                timeout=(30, 120),
            ) as response:
                response.raise_for_status()

                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)

            temporary_path.replace(destination_path)

        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
