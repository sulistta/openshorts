import json

import pytest

from raplifebr_pipeline import (
    HumanInterventionRequired,
    PipelineError,
    RightsNotConfirmed,
    add_candidate,
    approve_items,
    caption_for,
    confirm_caption,
    has_duplicate_clip,
    new_registry,
    publish_item,
    process_candidate,
    process_batch,
    transition,
    validate_probe,
)


class FakeMCP:
    def __init__(self, video_url):
        self.video_url = video_url
        self.calls = []

    def process_video(self, source_url, **kwargs):
        self.calls.append(("process_video", source_url, kwargs))
        return {"job_id": "job-1"}

    def get_job_status(self, job_id):
        self.calls.append(("get_job_status", job_id))
        return {"status": "completed"}

    def list_clips(self, job_id):
        self.calls.append(("list_clips", job_id))
        return {"clips": [{
            "index": 0,
            "title": "Best moment",
            "start": 12.5,
            "end": 42.5,
            "video_url": self.video_url,
            "viral_hook_text": "Esse momento!",
        }]}

    def add_subtitles(self, job_id, clip_index, **kwargs):
        self.calls.append(("add_subtitles", job_id, clip_index, kwargs))
        return {"new_video_url": self.video_url}


def _approved_candidate():
    return {
        "artist": "CJota",
        "track": "Faixa autorizada",
        "source_url": "https://example.test/owned.mp4",
        "rights_status": "approved",
        "license_proof": "licenses/cjota.txt",
    }


def test_add_candidate_is_idempotent_for_the_same_source():
    registry = new_registry()
    first = add_candidate(registry, _approved_candidate())
    second = add_candidate(registry, _approved_candidate())

    assert first["id"] == second["id"]
    assert len(registry["items"]) == 1
    assert first["status"] == "candidate"


def test_approve_items_records_explicit_user_attestation():
    registry = new_registry()
    first = add_candidate(registry, _approved_candidate())
    second = add_candidate(registry, {**_approved_candidate(), "track": "Outra faixa", "rights_status": "needs_license"})

    count = approve_items(registry, ids=[first["id"]], basis="explicit_user_attestation")

    assert count == 1
    assert first["rights_status"] == "approved"
    assert first["rights_basis"] == "explicit_user_attestation"
    assert first["rights_confirmed_at"]
    assert second["rights_status"] == "needs_license"


def test_transition_records_history_and_timestamp():
    item = add_candidate(new_registry(), _approved_candidate())
    transition(item, "processing", openshorts_job_id="job-1")

    assert item["status"] == "processing"
    assert item["openshorts_job_id"] == "job-1"
    assert item["history"][-1]["status"] == "processing"
    assert item["history"][-1]["at"]


def test_duplicate_clip_matches_artist_track_and_timestamp_only():
    registry = new_registry()
    item = add_candidate(registry, _approved_candidate())
    item.update({"clip_start": 12.5, "clip_end": 42.5})

    assert has_duplicate_clip(registry, "CJota", "Faixa autorizada", 12.5, 42.5)
    assert not has_duplicate_clip(registry, "CJota", "Faixa autorizada", 13.0, 42.5)
    assert not has_duplicate_clip(registry, "CJota", "Outra faixa", 12.5, 42.5)
    assert not has_duplicate_clip(registry, "CJota", "Faixa autorizada", 12.5, 42.5, exclude_id=item["id"])


def test_caption_varies_and_contains_artist_track_and_hashtag():
    first = caption_for("CJota", "Faixa autorizada", "Esse momento!", variant=0)
    second = caption_for("CJota", "Faixa autorizada", "Esse momento!", variant=1)

    assert first != second
    assert "CJota" in first
    assert "Faixa autorizada" in first
    assert "#cjota" in first.lower()
    assert "Esse momento!" in second


def test_confirm_caption_requires_expected_text_and_marks_verified():
    item = add_candidate(new_registry(), _approved_candidate())
    item["caption"] = "Uma legenda importante #rapbr"

    assert confirm_caption(item, "Uma legenda importante #rapbr") is True
    assert item["caption_verified"] is True
    assert confirm_caption(item, "Legenda diferente") is False
    assert item["caption_verified"] is False


def test_validate_probe_requires_vertical_video_audio_and_decode():
    good = validate_probe({
        "width": 1080,
        "height": 1920,
        "duration": 30.0,
        "has_audio": True,
    }, decode_ok=True)
    bad = validate_probe({
        "width": 1920,
        "height": 1080,
        "duration": 30.0,
        "has_audio": False,
    }, decode_ok=False)

    assert good["valid"] is True
    assert bad["valid"] is False
    assert bad["checks"]["vertical"] is False
    assert bad["checks"]["audio"] is False
    assert bad["checks"]["decode"] is False


def test_process_candidate_uses_mcp_then_marks_ready_to_publish(tmp_path):
    registry = new_registry()
    item = add_candidate(registry, _approved_candidate())
    mcp = FakeMCP(str(tmp_path / "final.mp4"))
    validation = {
        "valid": True,
        "checks": {"exists": True, "decode": True, "vertical": True, "audio": True},
    }

    result = process_candidate(
        item,
        registry,
        mcp,
        validate=lambda _path: validation,
        sleep=lambda _seconds: None,
        max_polls=2,
    )

    assert result["status"] == "ready_to_publish"
    assert result["openshorts_job_id"] == "job-1"
    assert result["clip_start"] == 12.5
    assert result["clip_end"] == 42.5
    assert result["caption"]
    assert [call[0] for call in mcp.calls] == [
        "process_video", "get_job_status", "list_clips"
    ]


def test_process_candidate_does_not_attest_rights_for_unlicensed_item():
    registry = new_registry()
    candidate = _approved_candidate()
    candidate["rights_status"] = "needs_license"
    item = add_candidate(registry, candidate)
    mcp = FakeMCP("/tmp/never-used.mp4")

    with pytest.raises(RightsNotConfirmed):
        process_candidate(item, registry, mcp, validate=lambda _path: {"valid": True})

    assert item["status"] == "candidate"
    assert mcp.calls == []


def test_publish_item_records_instagram_url_and_status():
    registry = new_registry()
    item = add_candidate(registry, _approved_candidate())
    item.update({"status": "ready_to_publish", "final_file": "/tmp/reel.mp4", "caption": "Legenda", "caption_verified": True})

    result = publish_item(item, registry, lambda path, caption: "https://www.instagram.com/reel/ABC123/")

    assert result["status"] == "published"
    assert result["instagram_url"].endswith("ABC123/")
    assert result["published_at"]


def test_publish_item_rejects_unverified_caption():
    registry = new_registry()
    item = add_candidate(registry, _approved_candidate())
    item.update({"status": "ready_to_publish", "final_file": "/tmp/reel.mp4", "caption": "Legenda"})

    with pytest.raises(PipelineError, match="caption has not been verified"):
        publish_item(item, registry, lambda _path, _caption: "https://www.instagram.com/reel/NEVER/")

    assert item["status"] == "ready_to_publish"


def test_publish_item_preserves_ready_state_for_human_intervention():
    registry = new_registry()
    item = add_candidate(registry, _approved_candidate())
    item.update({"status": "ready_to_publish", "final_file": "/tmp/reel.mp4", "caption": "Legenda", "caption_verified": True})

    with pytest.raises(HumanInterventionRequired):
        publish_item(item, registry, lambda _path, _caption: (_ for _ in ()).throw(
            HumanInterventionRequired("Instagram solicitou login ou 2FA")
        ))

    assert item["status"] == "ready_to_publish"
    assert "2FA" in item["error"]


def test_process_batch_skips_unlicensed_items_and_continues():
    registry = new_registry()
    blocked = add_candidate(registry, {
        "artist": "Sem licença",
        "track": "Bloqueada",
        "source_url": "https://example.test/blocked.mp4",
        "rights_status": "needs_license",
    })
    approved = add_candidate(registry, _approved_candidate())
    mcp = FakeMCP("/tmp/final.mp4")

    results = process_batch(
        registry,
        mcp,
        limit=10,
        validate=lambda _path: {"valid": True},
        sleep=lambda _seconds: None,
    )

    assert results["blocked"] == [blocked["id"]]
    assert results["processed"] == [approved["id"]]
    assert blocked["status"] == "candidate"
    assert approved["status"] == "ready_to_publish"
