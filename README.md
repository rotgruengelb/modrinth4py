# modrinth4py

A Python API client for the [Modrinth](https://modrinth.com) API.

## Installation

```bash
pip install modrinth4py
```

## Usage

```python
from modrinth4py import ModrinthClient, NewProject, SideSupport, ProjectType

client = ModrinthClient(token="your-modrinth-pat")

project = client.get_project("sodium")
print(project["title"])
```

### Creating a project

```python
from modrinth4py import NewProject, SideSupport, ProjectType, RequestedStatus

new_project = NewProject(
    slug="my-mod",
    title="My Mod",
    description="A short description",
    categories=["utility"],
    client_side=SideSupport.REQUIRED,
    server_side=SideSupport.OPTIONAL,
    body="## My Mod\nLong-form description here.",
    project_type=ProjectType.MOD,
)

result = client.create_project(new_project)
```

### Uploading a version

```python
from pathlib import Path
from modrinth4py import NewVersion, VersionType

version = NewVersion(
    name="1.0.0",
    version_number="1.0.0",
    project_id="your-id",
    game_versions=["1.21"],
    loaders=["fabric"],
    version_type=VersionType.RELEASE,
    changelog="Initial release.",
)

client.create_version(version, file_paths=[Path("my-mod-1.0.0.jar")])
```

### Modifying a project

```python
from modrinth4py import ModrinthClient, ProjectUpdate

client.modify_project("your-slug", ProjectUpdate(
    discord_url="https://discord.gg/your-invite",
    license_id="MIT",
))
```

### Parallel requests

```python
projects = ModrinthClient.parallel_requests([
    lambda: client.get_project("sodium"),
    lambda: client.get_project("lithium"),
    lambda: client.get_project("iris"),
])
```

### Logging

`modrinth4py` uses Python's standard `logging` module under the `modrinth4py.client` logger.
Pass `debug=True` to `ModrinthClient` to enable debug output, or configure your own handler:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## License

MIT