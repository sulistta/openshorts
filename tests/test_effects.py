from openshorts_mcp.effects import build_filter, normalize_edits


def test_structured_edits_are_bounded_and_sorted():
    edits = [
        {"type": "zoom_in", "start_seconds": 4, "end_seconds": 20, "strength": 9},
        {"type": "color_pop", "start_seconds": 1, "end_seconds": 3, "strength": 0.4},
        {"type": "not_a_real_effect", "start_seconds": 0, "end_seconds": 2},
    ]

    normalized = normalize_edits(edits, 10)

    assert [item["type"] for item in normalized] == ["color_pop", "zoom_in"]
    assert normalized[1]["end_seconds"] == 10
    assert normalized[1]["strength"] == 0.15


def test_text_layers_disable_reframing_effects_but_keep_color_edits():
    filter_graph, applied = build_filter(
        [
            {"type": "punch_in", "start_seconds": 0, "end_seconds": 2},
            {"type": "bw_moment", "start_seconds": 2, "end_seconds": 4},
        ],
        duration=8,
        fps=30,
        width=1080,
        height=1920,
        has_text_layers=True,
    )

    assert [item["type"] for item in applied] == ["bw_moment"]
    assert filter_graph is not None
    assert "zoompan" not in filter_graph
    assert "hue=s=0" in filter_graph
