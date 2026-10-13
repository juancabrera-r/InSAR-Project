from logging import getLogger
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.insar_platform.ingestion.cdse import CDSEIngestion


@pytest.fixture
def ingestion():
    return CDSEIngestion(
        config={"URL": "https://catalogue.example.com"},
        selected_pair=["product_1", "product_2"],
        env={
            "CDSE_USERNAME": "test_user",
            "CDSE_PASSWORD": "test_password",
        },
        logger=getLogger("test_cdse_ingestion"),
    )


@pytest.fixture
def mock_response():
    response = MagicMock()
    response.raise_for_status.return_value = None
    return response


# ============================================================
# Initialization
# ============================================================


def test_initialization(ingestion):
    assert ingestion.initial_url == "https://catalogue.example.com"
    assert ingestion.cdse_username == "test_user"
    assert ingestion.cdse_password == "test_password"
    assert ingestion.selected_pair == ["product_1", "product_2"]


# ============================================================
# Authentication
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.post")
def test_authenticate_success(mock_post, ingestion, mock_response):
    mock_response.json.return_value = {"access_token": "test_access_token"}
    mock_post.return_value = mock_response

    token = ingestion.authenticate()

    assert token == "test_access_token"

    mock_post.assert_called_once_with(
        "https://identity.dataspace.copernicus.eu/"
        "auth/realms/CDSE/protocol/openid-connect/token",
        data={
            "username": "test_user",
            "password": "test_password",
            "grant_type": "password",
            "client_id": "cdse-public",
        },
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


@patch("insar_platform.ingestion.cdse.requests.post")
def test_authenticate_http_error(mock_post, ingestion, mock_response):
    mock_response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    mock_post.return_value = mock_response

    with pytest.raises(requests.HTTPError):
        ingestion.authenticate()


@patch("insar_platform.ingestion.cdse.requests.post")
def test_authenticate_missing_access_token(mock_post, ingestion, mock_response):
    mock_response.json.return_value = {}
    mock_post.return_value = mock_response

    with pytest.raises(KeyError, match="access_token"):
        ingestion.authenticate()


@patch("insar_platform.ingestion.cdse.requests.post")
def test_authenticate_connection_error(mock_post, ingestion):
    mock_post.side_effect = requests.ConnectionError("Connection failed")

    with pytest.raises(requests.ConnectionError):
        ingestion.authenticate()


# ============================================================
# Product metadata
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_product_metadata_success(mock_get, ingestion, mock_response):
    metadata = {
        "Id": "product_1",
        "Name": "S1A_TEST_PRODUCT",
    }
    mock_response.json.return_value = metadata
    mock_get.return_value = mock_response

    result = ingestion.get_product_metadata("product_1")

    assert result == metadata

    mock_get.assert_called_once_with(
        "https://catalogue.example.com/odata/v1/Products(product_1)",
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_product_metadata_http_error(mock_get, ingestion, mock_response):
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    mock_get.return_value = mock_response

    with pytest.raises(requests.HTTPError):
        ingestion.get_product_metadata("invalid_product")


# ============================================================
# Product nodes
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_product_nodes_success(mock_get, ingestion, mock_response):
    nodes = {
        "result": [
            {
                "Name": "manifest.safe",
                "Nodes": {"uri": "https://example.com/Nodes"},
            }
        ]
    }
    mock_response.json.return_value = nodes
    mock_get.return_value = mock_response

    result = ingestion.get_product_nodes("product_1")

    assert result == nodes

    mock_get.assert_called_once_with(
        "https://catalogue.example.com/odata/v1/Products(product_1)/Nodes",
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_product_nodes_http_error(mock_get, ingestion, mock_response):
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    mock_get.return_value = mock_response

    with pytest.raises(requests.HTTPError):
        ingestion.get_product_nodes("product_1")


# ============================================================
# Children nodes
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_children_node_success(mock_get, ingestion, mock_response):
    product_nodes = {"result": [{"Nodes": {"uri": "https://example.com/root/Nodes"}}]}

    children = {
        "result": [
            {"Name": "annotation"},
            {"Name": "measurement"},
        ]
    }

    mock_response.json.return_value = children
    mock_get.return_value = mock_response

    result = ingestion.get_children_node(product_nodes)

    assert result == children

    mock_get.assert_called_once_with(
        "https://example.com/root/Nodes",
        timeout=30,
    )


def test_get_children_node_empty_result(ingestion):
    with pytest.raises(IndexError):
        ingestion.get_children_node({"result": []})


def test_get_children_node_missing_result(ingestion):
    with pytest.raises(KeyError, match="result"):
        ingestion.get_children_node({})


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_children_node_http_error(mock_get, ingestion, mock_response):
    mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
    mock_get.return_value = mock_response

    product_nodes = {"result": [{"Nodes": {"uri": "https://example.com/root/Nodes"}}]}

    with pytest.raises(requests.HTTPError):
        ingestion.get_children_node(product_nodes)


# ============================================================
# Directory files
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_directory_files_success(mock_get, ingestion, mock_response):
    children_node = {
        "result": [
            {
                "Name": "annotation",
                "Nodes": {"uri": "https://example.com/annotation/Nodes"},
            },
            {
                "Name": "measurement",
                "Nodes": {"uri": "https://example.com/measurement/Nodes"},
            },
        ]
    }

    directory_files = {"result": [{"Name": "annotation.xml"}]}

    mock_response.json.return_value = directory_files
    mock_get.return_value = mock_response

    result = ingestion.get_directory_files(
        children_node,
        "annotation",
    )

    assert result == directory_files

    mock_get.assert_called_once_with(
        "https://example.com/annotation/Nodes",
        timeout=30,
    )


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_directory_files_missing_directory(mock_get, ingestion):
    children_node = {
        "result": [
            {
                "Name": "measurement",
                "Nodes": {"uri": "https://example.com/measurement/Nodes"},
            }
        ]
    }

    with pytest.raises(
        ValueError,
        match="Directory 'annotation' not found",
    ):
        ingestion.get_directory_files(
            children_node,
            "annotation",
        )

    mock_get.assert_not_called()


@patch("insar_platform.ingestion.cdse.requests.get")
def test_get_directory_files_http_error(mock_get, ingestion, mock_response):
    mock_response.raise_for_status.side_effect = requests.HTTPError(
        "503 Service Unavailable"
    )
    mock_get.return_value = mock_response

    children_node = {
        "result": [
            {
                "Name": "annotation",
                "Nodes": {"uri": "https://example.com/annotation/Nodes"},
            }
        ]
    }

    with pytest.raises(requests.HTTPError):
        ingestion.get_directory_files(
            children_node,
            "annotation",
        )


# ============================================================
# Annotation download
# ============================================================


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_annotation_file_success(
    mock_auth,
    mock_get,
    ingestion,
    mock_response,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    mock_response.iter_content.return_value = [
        b"<xml>",
        b"test",
        b"</xml>",
    ]
    mock_get.return_value = mock_response

    destination = tmp_path / "annotation" / "test.xml"

    annotation_node = {"Nodes": {"uri": "https://example.com/test.xml/Nodes"}}

    ingestion.download_annotation_file(
        annotation_node,
        destination,
    )

    assert destination.is_file()
    assert destination.read_bytes() == b"<xml>test</xml>"

    mock_auth.assert_called_once()

    mock_get.assert_called_once_with(
        "https://example.com/test.xml/$value",
        headers={"Authorization": "Bearer test_token"},
        timeout=60,
    )

    mock_response.iter_content.assert_called_once_with(chunk_size=8192)


@patch.object(CDSEIngestion, "authenticate")
def test_download_annotation_file_invalid_uri(mock_auth, ingestion, tmp_path):
    annotation_node = {"Nodes": {"uri": "https://example.com/invalid"}}

    destination = tmp_path / "test.xml"

    with pytest.raises(
        ValueError,
        match="Unexpected CDSE node URI structure",
    ):
        ingestion.download_annotation_file(
            annotation_node,
            destination,
        )

    assert not destination.exists()
    mock_auth.assert_not_called()


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_annotation_file_http_error(
    mock_auth,
    mock_get,
    ingestion,
    mock_response,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
    mock_get.return_value = mock_response

    destination = tmp_path / "annotation.xml"

    annotation_node = {"Nodes": {"uri": "https://example.com/annotation.xml/Nodes"}}

    with pytest.raises(requests.HTTPError):
        ingestion.download_annotation_file(
            annotation_node,
            destination,
        )

    assert not destination.exists()


# ============================================================
# Measurement download
# ============================================================


@pytest.fixture
def measurement_node():
    return {"Nodes": {"uri": "https://example.com/measurement.tiff/Nodes"}}


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_success(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    response = MagicMock()
    response.__enter__.return_value = response
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [
        b"chunk_1",
        b"chunk_2",
        b"chunk_3",
    ]

    mock_get.return_value = response

    destination = tmp_path / "measurement" / "test.tiff"

    ingestion.download_measurement_file(
        measurement_node,
        destination,
    )

    assert destination.is_file()
    assert destination.read_bytes() == (b"chunk_1chunk_2chunk_3")

    assert not Path(str(destination) + ".part").exists()

    mock_auth.assert_called_once()

    mock_get.assert_called_once_with(
        "https://example.com/measurement.tiff/$value",
        headers={"Authorization": "Bearer test_token"},
        stream=True,
        timeout=(30, 120),
    )

    response.iter_content.assert_called_once_with(chunk_size=1024 * 1024)


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_existing_destination(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    destination = tmp_path / "existing.tiff"
    destination.write_bytes(b"existing_data")

    ingestion.download_measurement_file(
        measurement_node,
        destination,
    )

    assert destination.read_bytes() == b"existing_data"

    mock_auth.assert_not_called()
    mock_get.assert_not_called()


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_empty_chunks(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [
        b"first",
        b"",
        b"second",
        b"",
    ]

    mock_get.return_value = response

    destination = tmp_path / "test.tiff"

    ingestion.download_measurement_file(
        measurement_node,
        destination,
    )

    assert destination.read_bytes() == b"firstsecond"


@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_invalid_uri(
    mock_auth,
    ingestion,
    tmp_path,
):
    node = {"Nodes": {"uri": "https://example.com/invalid"}}

    destination = tmp_path / "test.tiff"

    with pytest.raises(
        ValueError,
        match="Unexpected CDSE node URI structure",
    ):
        ingestion.download_measurement_file(
            node,
            destination,
        )

    assert not destination.exists()
    mock_auth.assert_not_called()


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_http_error(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    response = MagicMock()
    response.__enter__.return_value = response
    response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    mock_get.return_value = response

    destination = tmp_path / "test.tiff"
    temporary = tmp_path / "test.tiff.part"

    with pytest.raises(requests.HTTPError):
        ingestion.download_measurement_file(
            measurement_node,
            destination,
        )

    assert not destination.exists()
    assert not temporary.exists()


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_interrupted_stream(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    def interrupted_stream(chunk_size):
        yield b"partial_data"
        raise requests.ConnectionError("Download interrupted")

    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.side_effect = interrupted_stream

    mock_get.return_value = response

    destination = tmp_path / "test.tiff"
    temporary = tmp_path / "test.tiff.part"

    with pytest.raises(
        requests.ConnectionError,
        match="Download interrupted",
    ):
        ingestion.download_measurement_file(
            measurement_node,
            destination,
        )

    assert not destination.exists()
    assert not temporary.exists()


@patch("insar_platform.ingestion.cdse.requests.get")
@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_replace_failure(
    mock_auth,
    mock_get,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.return_value = "test_token"

    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"valid_data"]

    mock_get.return_value = response

    destination = tmp_path / "test.tiff"
    temporary = tmp_path / "test.tiff.part"

    with (
        patch.object(
            Path,
            "replace",
            side_effect=OSError("Rename failed"),
        ),
        pytest.raises(OSError, match="Rename failed"),
    ):
        ingestion.download_measurement_file(
            measurement_node,
            destination,
        )

    assert not destination.exists()
    assert not temporary.exists()


@patch.object(CDSEIngestion, "authenticate")
def test_download_measurement_file_authentication_error(
    mock_auth,
    ingestion,
    measurement_node,
    tmp_path,
):
    mock_auth.side_effect = requests.HTTPError("Authentication failed")

    destination = tmp_path / "test.tiff"

    with pytest.raises(requests.HTTPError):
        ingestion.download_measurement_file(
            measurement_node,
            destination,
        )

    assert not destination.exists()
