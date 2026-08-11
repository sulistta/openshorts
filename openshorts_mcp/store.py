"""Durable local manifests for projects, artifacts and asynchronous jobs."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


STORE_VERSION = 2
MARKER_NAME = ".openshorts-mcp.json"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")


class StoreError(RuntimeError):
    """A local project-store operation could not be completed."""


class MigrationRequired(StoreError):
    """The configured output directory contains an OpenShorts legacy library."""


class NotFound(StoreError):
    """A requested local project, artifact or job does not exist."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".openshorts-mcp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def default_output_dir() -> Path:
    raw = os.environ.get("OPENSHORTS_OUTPUT_DIR")
    return Path(raw).expanduser() if raw else Path.cwd() / "output"


class Store:
    """Filesystem-only project state.

    Paths stored in manifests are relative to a project directory. Tool
    responses expand them to absolute local paths so MCP clients can hand them
    directly to their own tools.
    """

    def __init__(self, output_dir: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(output_dir or default_output_dir()).expanduser().resolve()
        self.marker_path = self.root / MARKER_NAME
        self.projects_root = self.root / "projects"
        self.jobs_root = self.root / "jobs"

    def initialize(self) -> None:
        if self.root.exists() and not self.root.is_dir():
            raise StoreError(f"OPENSHORTS_OUTPUT_DIR is not a directory: {self.root}")
        if not self.root.exists():
            self.root.mkdir(parents=True)

        marker = _read_json(self.marker_path)
        entries = [entry for entry in self.root.iterdir() if entry.name != MARKER_NAME]
        if marker is None and entries:
            raise MigrationRequired(
                f"{self.root} contains legacy OpenShorts files. Run "
                "openshorts-mcp migrate-legacy before starting the MCP server."
            )
        if marker is not None and marker.get("store_version") != STORE_VERSION:
            raise StoreError(
                f"Unsupported OpenShorts MCP store version in {self.marker_path}. "
                "Use a fresh output directory or migrate it with a compatible release."
            )
        if marker is None:
            _atomic_json(
                self.marker_path,
                {"store_version": STORE_VERSION, "created_at": now()},
            )
        self.projects_root.mkdir(exist_ok=True)
        self.jobs_root.mkdir(exist_ok=True)

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise StoreError(f"Invalid {label}.")
        return value

    def project_dir(self, project_id: str) -> Path:
        project_id = self._validate_id(project_id, "project_id")
        destination = (self.projects_root / project_id).resolve()
        if destination.parent != self.projects_root.resolve():
            raise StoreError("Invalid project path.")
        return destination

    def job_path(self, job_id: str) -> Path:
        job_id = self._validate_id(job_id, "job_id")
        return self.jobs_root / f"{job_id}.json"

    def create_project(self) -> dict[str, Any]:
        self.initialize()
        project_id = f"project-{uuid.uuid4().hex}"
        directory = self.project_dir(project_id)
        directory.mkdir(parents=True)
        for child in ("source", "analysis", "artifacts"):
            (directory / child).mkdir()
        manifest: dict[str, Any] = {
            "version": STORE_VERSION,
            "project_id": project_id,
            "created_at": now(),
            "updated_at": now(),
            "status": "importing",
            "source": None,
            "media": None,
            "analysis": {},
            "artifacts": [],
        }
        self._write_project(manifest)
        return manifest

    def _write_project(self, project: dict[str, Any]) -> None:
        project_id = self._validate_id(str(project.get("project_id") or ""), "project_id")
        project["updated_at"] = now()
        _atomic_json(self.project_dir(project_id) / "project.json", project)

    def get_project(self, project_id: str) -> dict[str, Any]:
        self.initialize()
        manifest = _read_json(self.project_dir(project_id) / "project.json")
        if not manifest:
            raise NotFound(f"Project not found: {project_id}")
        return manifest

    def update_project(
        self,
        project_id: str,
        update: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        update(project)
        self._write_project(project)
        return project

    def write_project_json(self, project_id: str, relative_path: str, value: dict[str, Any]) -> Path:
        path = self.project_path(project_id, relative_path)
        _atomic_json(path, value)
        return path

    def project_path(self, project_id: str, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise StoreError("Invalid relative project path.")
        directory = self.project_dir(project_id)
        target = (directory / relative).resolve()
        if directory not in target.parents and target != directory:
            raise StoreError("Invalid project path.")
        return target

    def abs_path(self, project_id: str, relative_path: str) -> str:
        return str(self.project_path(project_id, relative_path))

    def create_job(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        job_id = f"job-{uuid.uuid4().hex}"
        value: dict[str, Any] = {
            "job_id": job_id,
            "type": job_type,
            "status": "queued",
            "created_at": now(),
            "updated_at": now(),
            "payload": payload,
            "logs": [],
            "result": None,
            "error": None,
        }
        _atomic_json(self.job_path(job_id), value)
        return value

    def get_job(self, job_id: str) -> dict[str, Any]:
        self.initialize()
        value = _read_json(self.job_path(job_id))
        if not value:
            raise NotFound(f"Job not found: {job_id}")
        return value

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        value = self.get_job(job_id)
        value.update(changes)
        value["updated_at"] = now()
        _atomic_json(self.job_path(job_id), value)
        return value

    def append_job_log(self, job_id: str, message: str) -> None:
        value = self.get_job(job_id)
        logs = list(value.get("logs") or [])
        logs.append(str(message).strip()[:800])
        value["logs"] = logs[-50:]
        value["updated_at"] = now()
        _atomic_json(self.job_path(job_id), value)

    def add_artifact(self, project_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        artifact_id = self._validate_id(str(artifact.get("artifact_id") or ""), "artifact_id")

        def add(project: dict[str, Any]) -> None:
            entries = list(project.get("artifacts") or [])
            if any(item.get("artifact_id") == artifact_id for item in entries if isinstance(item, dict)):
                raise StoreError(f"Artifact already exists: {artifact_id}")
            entries.append(artifact)
            project["artifacts"] = entries
            project["status"] = "ready"

        return self.update_project(project_id, add)

    def find_artifact(self, artifact_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        artifact_id = self._validate_id(artifact_id, "artifact_id")
        for project in self.iter_projects(raw=True):
            for artifact in project.get("artifacts") or []:
                if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id:
                    return project, artifact
        raise NotFound(f"Artifact not found: {artifact_id}")

    def iter_projects(self, *, raw: bool = False) -> list[dict[str, Any]]:
        self.initialize()
        values: list[dict[str, Any]] = []
        for directory in self.projects_root.iterdir():
            if not directory.is_dir() or not ID_RE.fullmatch(directory.name):
                continue
            value = _read_json(directory / "project.json")
            if value:
                values.append(value if raw else self.public_project(value))
        values.sort(key=lambda value: str(value.get("updated_at") or ""), reverse=True)
        return values

    def public_artifact(self, project_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        public = dict(artifact)
        relative = public.pop("relative_path", None)
        if relative:
            public["path"] = self.abs_path(project_id, str(relative))
        return public

    def public_project(self, project: dict[str, Any]) -> dict[str, Any]:
        project_id = str(project["project_id"])
        public = {
            "project_id": project_id,
            "created_at": project.get("created_at"),
            "updated_at": project.get("updated_at"),
            "status": project.get("status"),
            "media": project.get("media"),
            "analysis": dict(project.get("analysis") or {}),
            "artifacts": [
                self.public_artifact(project_id, artifact)
                for artifact in project.get("artifacts") or []
                if isinstance(artifact, dict)
            ],
        }
        source = project.get("source")
        if isinstance(source, dict):
            source_public = dict(source)
            relative = source_public.pop("relative_path", None)
            if relative:
                source_public["path"] = self.abs_path(project_id, str(relative))
            public["source"] = source_public
        else:
            public["source"] = None
        for key in ("transcript_path", "contact_sheet_path"):
            relative = public["analysis"].get(key)
            if relative:
                public["analysis"][key] = self.abs_path(project_id, str(relative))
        return public

    def public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job.get("job_id"),
            "type": job.get("type"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "logs": list(job.get("logs") or [])[-12:],
            "result": job.get("result"),
            "error": job.get("error"),
        }

    def delete_project(self, project_id: str) -> None:
        directory = self.project_dir(project_id)
        if not directory.is_dir():
            raise NotFound(f"Project not found: {project_id}")
        shutil.rmtree(directory)


def migrate_legacy(output_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Move a legacy output directory aside, then create an empty MCP store.

    This intentionally does nothing automatically at server start. The caller
    must explicitly run the migration command before legacy data is renamed.
    """
    store = Store(output_dir)
    root = store.root
    cwd = Path.cwd().resolve()
    if root == root.parent or root == cwd:
        raise StoreError(
            "Refusing to migrate a filesystem root or the current directory. "
            "Set OPENSHORTS_OUTPUT_DIR to a dedicated output folder."
        )

    if not root.exists():
        store.initialize()
        return {"migrated": False, "output_dir": str(root), "reason": "output directory did not exist"}
    if not root.is_dir():
        raise StoreError(f"OPENSHORTS_OUTPUT_DIR is not a directory: {root}")

    marker = _read_json(root / MARKER_NAME)
    if marker and marker.get("store_version") == STORE_VERSION:
        return {
            "migrated": False,
            "output_dir": str(root),
            "reason": "already an OpenShorts MCP output directory",
        }

    entries = list(root.iterdir())
    if not entries:
        store.initialize()
        return {"migrated": False, "output_dir": str(root), "reason": "output directory was empty"}

    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = root.with_name(f"{root.name}-legacy-{suffix}")
    sequence = 2
    while destination.exists():
        destination = root.with_name(f"{root.name}-legacy-{suffix}-{sequence}")
        sequence += 1
    root.rename(destination)
    store.initialize()
    return {
        "migrated": True,
        "output_dir": str(root),
        "legacy_output_dir": str(destination),
    }
