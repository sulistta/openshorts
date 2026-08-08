"""The job status API should retain the useful error lines from a long log."""
import pytest

app = pytest.importorskip("app")


def test_error_lines_are_selected_over_trailing_noise():
    logs = [
        "🤖 Analyzing with Gemini...",
        "❌ Gemini Error: empty response body.",
        "🎬 Scene engine finished",
        "Process failed with exit code 1",
    ]
    text = app._job_error_text(logs)
    assert "Gemini Error" in text
    assert "Process failed" in text


def test_falls_back_to_tail_when_nothing_looks_like_an_error():
    logs = [f"line {i}" for i in range(20)]
    assert app._job_error_text(logs) == " ".join(f"line {i}" for i in range(10, 20))


def test_keeps_only_the_most_recent_errors():
    logs = [f"❌ error {i}" for i in range(12)]
    out = app._job_error_text(logs)
    assert "❌ error 11" in out
    assert "❌ error 0" not in out
