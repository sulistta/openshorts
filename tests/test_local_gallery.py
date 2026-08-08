from local_gallery import delete_actor, delete_video, list_actors, list_videos, save_actor, save_video


def test_gallery_assets_are_local_and_explicitly_deletable(tmp_path):
    actor_source = tmp_path / "actor.png"
    actor_source.write_bytes(b"png")
    actor = save_actor(str(tmp_path), str(actor_source), description="demo")
    assert actor["url"].startswith("/videos/gallery/actors/")
    assert list_actors(str(tmp_path))[0]["id"] == actor["id"]

    video_source = tmp_path / "video.mp4"
    video_source.write_bytes(b"video")
    video = save_video(str(tmp_path), str(video_source), str(actor_source), {"title": "demo"}, video_id="video1")
    assert video["video_url"] == "/videos/gallery/videos/video1/video.mp4"
    assert list_videos(str(tmp_path))[0]["video_id"] == "video1"

    assert delete_actor(str(tmp_path), actor["id"])
    assert delete_video(str(tmp_path), "video1")
    assert list_actors(str(tmp_path)) == []
    assert list_videos(str(tmp_path)) == []
