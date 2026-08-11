from pathlib import Path

from openshorts_mcp.server import create_server


def test_server_exposes_only_local_mcp_tools(tmp_path: Path):
    server = create_server(str(tmp_path / "output"))
    names = {tool.name for tool in server._tool_manager.list_tools()}

    assert names == {
        "import_media",
        "get_job_status",
        "list_projects",
        "get_project",
        "read_transcript",
        "get_contact_sheet",
        "render_clips",
        "apply_effects",
        "add_subtitles",
        "add_hook_overlay",
        "delete_project",
    }
    assert "process_video" not in names
    assert "publish_clip" not in names


def test_render_contract_requires_explicit_layout_and_format(tmp_path: Path):
    server = create_server(str(tmp_path / "output"))
    render = next(tool for tool in server._tool_manager.list_tools() if tool.name == "render_clips")
    schema = render.parameters

    assert set(schema["properties"]) == {"project_id", "clips"}
    assert set(schema["required"]) == {"project_id", "clips"}
