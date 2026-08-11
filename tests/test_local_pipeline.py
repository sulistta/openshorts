import shutil
import subprocess
from pathlib import Path

import pytest

from openshorts_mcp.store import Store
from openshorts_mcp.worker import run_job


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required for the local pipeline test")
def test_silent_local_import_and_render_keep_subtitles_off_by_default(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=5",
            "-t",
            "16",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    store = Store(tmp_path / "output")
    project = store.create_project()
    project_id = project["project_id"]
    imported = store.create_job(
        "import_media",
        {"project_id": project_id, "source_path": str(source), "source_url": None},
    )
    assert run_job(str(store.root), imported["job_id"]) == 0
    assert store.get_job(imported["job_id"])["status"] == "completed"

    rendered = store.create_job(
        "render_clips",
        {
            "project_id": project_id,
            "clips": [
                {
                    "start_seconds": 0,
                    "end_seconds": 15,
                    "output_format": "vertical",
                    "layout": "center_crop",
                    "label": "test",
                }
            ],
        },
    )
    assert run_job(str(store.root), rendered["job_id"]) == 0
    result = store.get_job(rendered["job_id"])
    artifact = result["result"]["artifacts"][0]

    assert result["status"] == "completed"
    assert Path(artifact["path"]).is_file()
    assert artifact["layers"] == []
