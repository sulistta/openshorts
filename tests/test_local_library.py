import json

from local_library import (
    MANIFEST_NAME,
    ensure_project,
    history,
    project_clip,
    recover_job,
    remove_project,
    save_project_state,
)


def _clip(name="clip.mp4"):
    return {"title": "Test clip", "video_url": f"/videos/job/{name}", "start": 0, "end": 2}


def test_project_manifest_survives_restart_and_tracks_edits(tmp_path):
    output = str(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "clip.mp4").write_bytes(b"video")
    (job_dir / "job_metadata.json").write_text(json.dumps({"shorts": [_clip()]}))
    ensure_project(output, "job", [_clip()])

    loaded = recover_job(output, "job")
    assert loaded["clips"][0]["video_url"] == "/videos/job/clip.mp4"
    assert (job_dir / MANIFEST_NAME).is_file()

    assert save_project_state(output, "job", [{"index": 0, "active_layers": {"hook": "x"}, "server_file": "clip.mp4"}])
    assert history(output)[0]["download_url"].endswith("/api/projects/job/clips/0/download")
    assert project_clip(output, "job", 0)[2].endswith("clip.mp4")

    manifest = json.loads((job_dir / MANIFEST_NAME).read_text())
    assert manifest["state"]["clips"][0]["active_layers"] == {"hook": "x"}


def test_project_delete_is_explicit_and_recoverable_until_called(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "clip.mp4").write_bytes(b"video")
    (job_dir / "job_metadata.json").write_text(json.dumps({"shorts": [_clip()]}))
    ensure_project(str(tmp_path), "job", [_clip()])
    assert recover_job(str(tmp_path), "job")
    assert remove_project(str(tmp_path), "job")
    assert recover_job(str(tmp_path), "job") is None
