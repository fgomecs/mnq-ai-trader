"""
Regression: dashboard live-patch writes were clobbering bars1min /
bars5min with empty arrays every 10s, blanking the chart between full
snapshot writes.

Two fixes verified here:
  1. dashboard_writer preserves existing bars when the incoming snapshot
     doesn't carry them.
  2. main._patch_dashboard_live now includes bars_1min/bars_5min in its
     snapshot dict so the writer never sees an empty list from this caller.
"""
import json

import pytest


def test_dashboard_writer_preserves_bars_when_snapshot_omits_them(tmp_path, monkeypatch):
    """Write 1: full snapshot with 3 bars. Write 2: snapshot WITHOUT bars
    keys (mimicking the old _patch_dashboard_live behavior). Result: bars
    must survive on disk so the chart stays populated."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))

    full_bars = [
        {"t": "2026-05-27 09:30", "o": 30000.0, "h": 30005.0, "l": 29998.0,
         "c": 30003.0, "v": 100, "vwap": 30001.0, "forming": False},
        {"t": "2026-05-27 09:31", "o": 30003.0, "h": 30008.0, "l": 30001.0,
         "c": 30007.0, "v": 120, "vwap": 30002.0, "forming": False},
        {"t": "2026-05-27 09:32", "o": 30007.0, "h": 30010.0, "l": 30005.0,
         "c": 30009.0, "v": 130, "vwap": 30003.5, "forming": True},
    ]
    dw.update_dashboard(
        position=0, current_price=30009.0, daily_pnl=0.0, trades=[],
        last_decision="HOLD", last_reasoning="full write",
        snapshot={"last_price": 30009.0, "bars_1min": full_bars, "bars_5min": []},
    )
    first = json.loads(target.read_text(encoding="utf-8"))
    assert len(first["bars1min"]) == 3, "full write should land 3 bars"

    # Second write: simulate the legacy live-patch behavior with no bars.
    dw.update_dashboard(
        position=0, current_price=30010.0, daily_pnl=0.0, trades=[],
        last_decision="HOLD", last_reasoning="patch write",
        snapshot={"last_price": 30010.0},   # NO bars keys
    )
    second = json.loads(target.read_text(encoding="utf-8"))
    assert len(second["bars1min"]) == 3, \
        f"patch write must NOT clobber bars1min — got {len(second['bars1min'])} bars"


def test_dashboard_writer_accepts_new_bars_on_subsequent_write(tmp_path, monkeypatch):
    """The preservation rule must not block a legitimate update — when
    the snapshot DOES carry bars, they replace the old list."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))

    write1 = [{"t": "2026-05-27 09:30", "o": 30000.0, "h": 30005.0,
               "l": 29998.0, "c": 30003.0, "v": 100, "vwap": 30001.0,
               "forming": False}]
    write2 = write1 + [
        {"t": "2026-05-27 09:31", "o": 30003.0, "h": 30008.0, "l": 30001.0,
         "c": 30007.0, "v": 120, "vwap": 30002.0, "forming": False},
    ]
    dw.update_dashboard(position=0, current_price=30003.0, daily_pnl=0.0,
                        trades=[], last_decision="HOLD", last_reasoning="1",
                        snapshot={"last_price": 30003.0, "bars_1min": write1, "bars_5min": []})
    dw.update_dashboard(position=0, current_price=30007.0, daily_pnl=0.0,
                        trades=[], last_decision="HOLD", last_reasoning="2",
                        snapshot={"last_price": 30007.0, "bars_1min": write2, "bars_5min": []})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["bars1min"]) == 2


def test_patch_dashboard_live_includes_bars_in_snapshot():
    """main._patch_dashboard_live source must read feed._bars_1min /
    feed._bars_5min and put them in the snapshot dict it passes to
    update_dashboard. The fix is the absence of empty-bars writes."""
    import main as _main
    import inspect
    src = inspect.getsource(_main._patch_dashboard_live)
    assert '"bars_1min"' in src, \
        "_patch_dashboard_live must include bars_1min in its snapshot dict"
    assert '"bars_5min"' in src, \
        "_patch_dashboard_live must include bars_5min in its snapshot dict"
    # And it should source from the feed, not pass empty list literals
    assert "_serialize_bars_with_vwap" in src, \
        "_patch_dashboard_live should call feed._serialize_bars_with_vwap"
