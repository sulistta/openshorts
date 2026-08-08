"""Build the Python backend as the architecture-named Tauri sidecar."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
BINARIES = DASHBOARD / "src-tauri" / "binaries"
BUILD_ROOT = ROOT / "build" / "pyinstaller"


def target_triple() -> str:
    configured = os.environ.get("OPENSHORTS_TARGET_TRIPLE") or os.environ.get("TAURI_ENV_TARGET_TRIPLE")
    if configured:
        return configured
    return subprocess.check_output(
        ["rustc", "--print", "host-tuple"], text=True
    ).strip()


def add_data(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def main() -> None:
    target = target_triple()
    suffix = ".exe" if os.name == "nt" else ""
    dist_dir = BUILD_ROOT / "dist"
    work_dir = BUILD_ROOT / "work"
    spec_dir = BUILD_ROOT / "spec"
    BINARIES.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "openshorts-backend",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(ROOT),
        "--add-data",
        add_data(ROOT / "fonts", "fonts"),
        "--add-data",
        add_data(ROOT / "assets", "assets"),
        "--hidden-import",
        "app",
        "--hidden-import",
        "main",
        "--hidden-import",
        "quality_probe",
        "--hidden-import",
        "mcp_server",
    ]

    # These packages load native modules and model metadata dynamically. Their
    # complete package data must be present in the standalone backend.
    for package in (
        "av",
        "ctranslate2",
        "faster_whisper",
        "google.genai",
        "mediapipe",
        "scenedetect",
        "tokenizers",
        "torch",
        "torchvision",
        "transnetv2_pytorch",
        "ultralytics",
        "yt_dlp",
    ):
        command.extend(["--collect-all", package])

    command.append(str(ROOT / "desktop" / "backend.py"))
    subprocess.run(command, check=True, cwd=ROOT)

    built = dist_dir / f"openshorts-backend{suffix}"
    destination = BINARIES / f"openshorts-backend-{target}{suffix}"
    shutil.copy2(built, destination)
    print(f"Built desktop sidecar: {destination}")


if __name__ == "__main__":
    main()
