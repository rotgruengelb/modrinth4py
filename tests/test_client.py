from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modrinth4py import (
    ModrinthClient,
    ModrinthAPIError,
    NewProject,
    NewVersion,
    ProjectUpdate,
    SideSupport,
    ProjectType,
    VersionType,
    VersionStatus,
    RequestedStatus,
)

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def make_client() -> ModrinthClient:
    return ModrinthClient(token="test-token", user_agent="modrinth4py-tests/0.0.0")


def test_client_instantiation():
    client = make_client()
    assert client.api_url == ModrinthClient.DEFAULT_API_URL


def test_modrinth_api_error_carries_status():
    err = ModrinthAPIError("something went wrong", status_code=404, response={"error": "not_found"})
    assert err.status_code == 404
    assert err.response["error"] == "not_found"


def test_new_project_defaults():
    project = NewProject(
        slug="my-mod",
        title="My Mod",
        description="A test mod",
        categories=["utility"],
        client_side=SideSupport.REQUIRED,
        server_side=SideSupport.OPTIONAL,
        body="## My Mod\nA description.",
        project_type=ProjectType.MOD,
    )
    assert project.is_draft is True
    assert project.requested_status == RequestedStatus.UNLISTED


def test_new_version_defaults():
    version = NewVersion(
        name="1.0.0",
        version_number="1.0.0",
        project_id="AAAAAAA",
        game_versions=["1.21"],
        loaders=["fabric"],
        version_type=VersionType.RELEASE,
    )
    assert version.featured is True
    assert version.status == VersionStatus.LISTED


def test_to_dict_excludes_none():
    update = ProjectUpdate(title="New Title")
    result = ModrinthClient._to_dict(update)
    assert "title" in result
    assert "slug" not in result


def test_parallel_requests_preserves_order():
    results = ModrinthClient.parallel_requests([
        lambda: 1,
        lambda: 2,
        lambda: 3,
    ])
    assert results == [1, 2, 3]


def test_parallel_requests_raises_on_error():
    def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        ModrinthClient.parallel_requests([failing])


@patch("modrinth4py.client.requests.Session.send")
def test_get_project_calls_correct_endpoint(mock_send):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"id": "AAAAAAA", "slug": "sodium"}'
    mock_response.json.return_value = {"id": "AAAAAAA", "slug": "sodium"}
    mock_response.headers = {
        "X-Ratelimit-Limit": "300",
        "X-Ratelimit-Remaining": "299",
        "X-Ratelimit-Reset": "0",
    }
    mock_response.content = b'{"id": "AAAAAAA"}'
    mock_send.return_value = mock_response

    client = make_client()
    result = client.get_project("sodium")

    assert result["slug"] == "sodium"
    called_url = mock_send.call_args[0][0].url
    assert "/v2/project/sodium" in called_url