"""Entrypoint for the local OpenShorts backend sidecar.

The same executable serves three roles after PyInstaller bundles it: the
FastAPI server, the video-processing worker and the quality probe. Keeping
them in one binary lets the desktop bundle work without a separately installed
Python interpreter.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def run_module(module: str, argv: list[str]) -> None:
    sys.argv = [f"{module}.py", *argv]
    runpy.run_module(module, run_name="__main__")


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--quality-probe", action="store_true")
    parser.add_argument("--port", type=int, default=37831)
    parser.add_argument("--data-dir")
    args, remainder = parser.parse_known_args()

    if args.worker:
        run_module("main", remainder)
        return
    if args.quality_probe:
        run_module("quality_probe", remainder)
        return

    resources = resource_dir()
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else resources
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("OPENSHORTS_RESOURCE_DIR", str(resources))
    os.environ.setdefault("OPENSHORTS_DATA_DIR", str(data_dir))
    os.environ.setdefault("OPENSHORTS_FROZEN", "1" if getattr(sys, "frozen", False) else "0")
    os.chdir(data_dir)
    sys.path.insert(0, str(resources))

    import uvicorn
    from app import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
