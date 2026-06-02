from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path
from typing import Optional, List, Any, IO, Iterator, Callable, Dict

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry
from importlib.metadata import version, PackageNotFoundError

from modrinth4py.exceptions import ModrinthAPIError
from modrinth4py.types import (
    DictKV,
    ListDictKV,
    NewProject,
    NewVersion,
    GalleryImage,
    ProjectUpdate,
    VersionUpdate,
)

logger = logging.getLogger(__name__)

try:
    __version__ = version("modrinth4py")
except PackageNotFoundError:
    __version__ = "unknown"

def _cut_game_versions_until(cutoff_version: str, versions: List[DictKV]) -> List[DictKV]:
    result: List[DictKV] = []
    for version in versions:
        result.append(version)
        if version.get("version") == cutoff_version:
            break
    return result


class ModrinthClient:
    """
    HTTP client for the Modrinth API.

    Rate limiting is handled automatically. Requests are retried on 429/5xx responses.
    For concurrent workloads, use :meth:`parallel_requests`.

    :param token: Modrinth API token (PAT).
    :param api_url: Base URL for the API. Defaults to ``https://api.modrinth.com``.
    :param user_agent: Value sent as the ``User-Agent`` header.
    :param debug: Enable debug-level request/response logging via stdlib ``logging``.
    :param verbose_debug: Also log full request bodies (implies ``debug=True``).
    """

    DEFAULT_API_URL = "https://api.modrinth.com"

    def __init__(
        self,
        token: str,
        api_url: str = DEFAULT_API_URL,
        user_agent: str = f"modrinth4py/{__version__}",
        debug: bool = False,
        verbose_debug: bool = False,
    ) -> None:
        self.api_url = api_url
        self._debug = debug or verbose_debug
        self._verbose_debug = verbose_debug

        self._session = requests.Session()
        self._session.headers.update({"Authorization": token, "User-Agent": user_agent})

        self._ratelimit_lock = threading.Lock()
        self._ratelimit_limit = 300
        self._ratelimit_remaining = 300
        self._ratelimit_reset = 0

    def _make_thread_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(self._session.headers)
        s.headers["Connection"] = "close"

        retry = Retry(
            total=10,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
        s.mount("https://", adapter)
        return s

    def _update_ratelimit(self, response: requests.Response) -> None:
        with self._ratelimit_lock:
            try:
                self._ratelimit_limit = int(response.headers.get("X-Ratelimit-Limit", self._ratelimit_limit))
                self._ratelimit_remaining = int(response.headers.get("X-Ratelimit-Remaining", self._ratelimit_remaining))
                self._ratelimit_reset = int(response.headers.get("X-Ratelimit-Reset", 0))
            except (ValueError, TypeError):
                pass

            if self._debug:
                logger.debug(
                    "rate limit updated: limit=%d remaining=%d reset=%d",
                    self._ratelimit_limit,
                    self._ratelimit_remaining,
                    self._ratelimit_reset,
                )

    def _respect_ratelimit(self) -> None:
        with self._ratelimit_lock:
            if self._ratelimit_remaining > 0:
                return

            sleep_time = self._ratelimit_reset
            logger.warning("rate limited - sleeping %d seconds", sleep_time)
            self._ratelimit_remaining = self._ratelimit_limit
            self._ratelimit_reset = 0

        if sleep_time > 0:
            time.sleep(sleep_time)

    def _log_request(self, prepped: requests.PreparedRequest) -> None:
        logger.debug("%s %s", prepped.method, prepped.url)
        for k, v in prepped.headers.items():
            if k.lower() == "authorization":
                v = f"{v[:10]}..." if len(v) > 10 else "***"
            logger.debug("  %s: %s", k, v)

        if prepped.body is None:
            logger.debug("  body: (none)")
        elif "json" in prepped.headers.get("Content-Type", "").lower() and isinstance(prepped.body, bytes):
            try:
                logger.debug("  body: %s", json.dumps(json.loads(prepped.body.decode()), indent=2))
            except Exception:
                logger.debug("  body (raw): %r", prepped.body)
        elif isinstance(prepped.body, (str, bytes)):
            preview = prepped.body[:200] if isinstance(prepped.body, str) else prepped.body[:200].decode(errors="replace")
            logger.debug("  body: %s", preview)
        else:
            logger.debug("  body: <binary/multipart>")

    def _request(self, method: str, endpoint: str, api_version: int = 2, **kwargs) -> Any:
        url = f"{self.api_url}/v{api_version}{endpoint}"

        thread = threading.current_thread()
        session: Optional[requests.Session] = getattr(thread, "_modrinth4py_session", None)
        if session is None:
            session = self._make_thread_session()
            setattr(thread, "_modrinth4py_session", session)

        self._respect_ratelimit()

        response: Optional[requests.Response] = None
        try:
            prepped = session.prepare_request(requests.Request(method, url, **kwargs))

            if self._verbose_debug:
                self._log_request(prepped)
            elif self._debug:
                logger.debug("%s %s", method, url)

            settings = session.merge_environment_settings(prepped.url, {}, None, None, None)
            response = session.send(prepped, **settings)

            if self._debug:
                logger.debug("response: status=%d length=%d", response.status_code, len(response.content))

            self._update_ratelimit(response)
            response.raise_for_status()
            return response.json() if response.text else {}

        except requests.HTTPError as exc:
            if response is not None:
                logger.error("HTTP %d: %s", response.status_code, response.text[:500])
            try:
                error_body = response.json() if response is not None else {}
            except Exception:
                error_body = {"error": response.text if response else "no response"}

            raise ModrinthAPIError(
                str(exc),
                status_code=response.status_code if response is not None else None,
                response=error_body,
            ) from exc

    @staticmethod
    def _to_dict(obj: Any) -> DictKV:
        return {k: v for k, v in obj.__dict__.items() if v is not None}

    @staticmethod
    def _open_files(paths: List[Path]) -> Iterator[Dict[str, IO[bytes]]]:
        with ExitStack() as stack:
            yield {f"file{i}": stack.enter_context(p.open("rb")) for i, p in enumerate(paths)}

    def get_project(self, id_or_slug: str) -> DictKV:
        """Fetch a project by its ID or slug."""
        return self._request("GET", f"/project/{id_or_slug}")

    def create_project(self, project: NewProject, icon_path: Optional[Path] = None) -> DictKV:
        """Create a new project. Optionally attach an icon."""
        payload = self._to_dict(project)
        if "donation_urls" in payload:
            payload["donation_urls"] = [du.__dict__ for du in payload["donation_urls"]]

        files: dict = {"data": (None, json.dumps(payload), "application/json")}
        if icon_path:
            files["icon"] = icon_path.open("rb")

        try:
            return self._request("POST", "/project", files=files)
        finally:
            if icon_path:
                files["icon"].close()

    def modify_project(self, id_or_slug: str, update: ProjectUpdate) -> None:
        """Partially update a project."""
        self._request("PATCH", f"/project/{id_or_slug}", json=self._to_dict(update))

    def change_project_icon(self, id_or_slug: str, icon_path: Path, ext: str) -> None:
        """Replace the icon of a project."""
        with icon_path.open("rb") as icon_file:
            self._request("PATCH", f"/project/{id_or_slug}/icon", params={"ext": ext}, data=icon_file)

    def get_project_versions(self, id_or_slug: str) -> ListDictKV:
        """List all versions of a project."""
        return self._request("GET", f"/project/{id_or_slug}/version")

    def get_version(self, version_id: str) -> DictKV:
        """Fetch a single version by its ID."""
        return self._request("GET", f"/version/{version_id}")

    def create_version(
        self,
        version: NewVersion,
        file_paths: List[Path],
        primary_file: Optional[str] = None,
    ) -> DictKV:
        """Upload a new version with one or more files."""
        with ExitStack() as stack:
            files = {f"file{i}": stack.enter_context(p.open("rb")) for i, p in enumerate(file_paths)}
            payload: DictKV = {**version.__dict__, "file_parts": list(files.keys())}
            if primary_file:
                payload["primary_file"] = primary_file
            return self._request("POST", "/version", data={"data": json.dumps(payload)}, files=files)

    def modify_version(self, version_id: str, update: VersionUpdate) -> None:
        """Partially update a version."""
        self._request("PATCH", f"/version/{version_id}", json=self._to_dict(update))

    def delete_version(self, version_id: str) -> None:
        """Delete a version permanently."""
        self._request("DELETE", f"/version/{version_id}")

    def add_gallery_image(self, id_or_slug: str, image: GalleryImage) -> None:
        """Upload an image to a project's gallery."""
        params: DictKV = {"ext": image.ext, "featured": str(image.featured).lower()}
        if image.title:
            params["title"] = image.title
        if image.description:
            params["description"] = image.description
        if image.ordering is not None:
            params["ordering"] = image.ordering

        with image.image_path.open("rb") as img_file:
            self._request("POST", f"/project/{id_or_slug}/gallery", params=params, data=img_file)

    def delete_gallery_image(self, id_or_slug: str, image_url: str) -> None:
        """Remove an image from a project's gallery by its URL."""
        self._request("DELETE", f"/project/{id_or_slug}/gallery", params={"url": image_url})

    def get_organization_projects(self, organization_id: str) -> ListDictKV:
        """List all projects belonging to an organization."""
        return self._request("GET", f"/organization/{organization_id}/projects", api_version=3)

    def get_game_versions(self) -> ListDictKV:
        """Return all known game versions from the Modrinth tag API."""
        return self._request("GET", "/tag/game_version")

    def get_game_versions_until(self, cutoff_version: str) -> ListDictKV:
        """Return game versions from newest down to and including ``cutoff_version``."""
        return _cut_game_versions_until(cutoff_version, self.get_game_versions())

    def get_loaders(self) -> ListDictKV:
        """Return all known mod loaders from the Modrinth tag API."""
        return self._request("GET", "/tag/loader")

    @staticmethod
    def parallel_requests(requests_list: List[Callable[[], Any]], max_parallel: int = 6) -> List[Any]:
        """
        Execute a list of callables in parallel using a thread pool.

        Each callable should be a zero-argument function that calls one of the
        client methods (e.g. ``lambda: client.get_project("sodium")``).
        Results are returned in the same order as the input list.
        Raises the first exception encountered, if any.
        """
        results: List[Any] = [None] * len(requests_list)

        def _wrap(fn: Callable[[], Any]) -> Any:
            try:
                return fn()
            except Exception as exc:
                return exc

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            future_to_index = {executor.submit(_wrap, fn): i for i, fn in enumerate(requests_list)}
            for future in as_completed(future_to_index):
                results[future_to_index[future]] = future.result()

        for result in results:
            if isinstance(result, Exception):
                raise result

        return results