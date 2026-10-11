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

    def get_children_node_annotation(self, children_node: dict) -> dict:

        annotation_node = next(
            node for node in children_node["result"] if node["Name"] == "annotation"
        )

        annotation_url = annotation_node["Nodes"]["uri"]

        response = requests.get(annotation_url, timeout=30)
        response.raise_for_status()

        annotation_files = response.json()

        return annotation_files

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
