from pathlib import Path

import pytest

from openshorts_mcp.store import MigrationRequired, Store, StoreError, migrate_legacy


def test_legacy_output_requires_explicit_migration(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "old-metadata.json").write_text("{}", encoding="utf-8")

    with pytest.raises(MigrationRequired):
        Store(output).initialize()

    result = migrate_legacy(output)

    assert result["migrated"] is True
    assert output.is_dir()
    assert (output / ".openshorts-mcp.json").is_file()
    legacy = Path(result["legacy_output_dir"])
    assert (legacy / "old-metadata.json").is_file()


def test_store_projects_expose_absolute_artifact_paths(tmp_path: Path):
    store = Store(tmp_path / "mcp-output")
    project = store.create_project()
    project_id = project["project_id"]
    source = store.project_path(project_id, "source/source.mp4")
    source.write_bytes(b"fake")

    def mark_ready(value):
        value["status"] = "ready"
        value["source"] = {"relative_path": "source/source.mp4", "kind": "local_file"}
        value["media"] = {"duration_seconds": 30}
        value["artifacts"] = [
            {
                "artifact_id": "artifact-12345678",
                "relative_path": "artifacts/artifact-12345678.mp4",
                "kind": "clip",
            }
        ]

    store.update_project(project_id, mark_ready)
    public = store.public_project(store.get_project(project_id))

    assert Path(public["source"]["path"]).is_absolute()
    assert public["artifacts"][0]["path"].endswith("artifact-12345678.mp4")


def test_store_rejects_project_path_traversal(tmp_path: Path):
    store = Store(tmp_path / "mcp-output")
    project = store.create_project()

    with pytest.raises(StoreError):
        store.project_path(project["project_id"], "../outside")
