import pytest

from openshorts_mcp.media import MediaError, contact_sheet_timestamps


def test_contact_sheet_uses_evenly_distributed_times():
    values = contact_sheet_timestamps(100, start_seconds=20, end_seconds=80, count=3)

    assert values == [30.0, 50.0, 70.0]


def test_contact_sheet_rejects_too_many_frames():
    with pytest.raises(MediaError):
        contact_sheet_timestamps(100, timestamps=list(range(25)))
