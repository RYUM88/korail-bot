"""목표 시각 파싱과 정밀 대기 테스트."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from korail_bot.scheduler import (  # noqa: E402
    KST,
    TargetSpecError,
    format_epoch,
    parse_duration,
    parse_target,
    resync_checkpoints,
    sleep_until_mono,
)

# 2026-09-15 13:00:00 KST
NOW = datetime(2026, 9, 15, 13, 0, 0, tzinfo=KST).timestamp()


def kst(*args) -> float:
    return datetime(*args, tzinfo=KST).timestamp()


def test_parse_absolute_datetime_defaults_to_kst():
    assert parse_target("2026-09-15 14:00:00", now=NOW) == kst(2026, 9, 15, 14, 0, 0)
    assert parse_target("2026-09-15 14:00", now=NOW) == kst(2026, 9, 15, 14, 0, 0)
    assert parse_target("2026/09/15 14:00:00", now=NOW) == kst(2026, 9, 15, 14, 0, 0)
    assert parse_target("20260915 140000", now=NOW) == kst(2026, 9, 15, 14, 0, 0)


def test_parse_iso_with_explicit_timezone_is_respected():
    # 05:00 UTC == 14:00 KST
    utc = parse_target("2026-09-15T05:00:00+00:00", now=NOW)
    assert utc == kst(2026, 9, 15, 14, 0, 0)


def test_parse_time_only_uses_today():
    assert parse_target("14:00", now=NOW) == kst(2026, 9, 15, 14, 0, 0)
    assert parse_target("14:00:30", now=NOW) == kst(2026, 9, 15, 14, 0, 30)


def test_parse_time_only_rolls_over_to_tomorrow_when_past():
    # 지금이 13:00 인데 09:00 을 넣으면 내일 09:00 이어야 한다.
    assert parse_target("09:00", now=NOW) == kst(2026, 9, 16, 9, 0, 0)


def test_parse_relative_offsets():
    assert parse_target("+90s", now=NOW) == pytest.approx(NOW + 90)
    assert parse_target("+5m", now=NOW) == pytest.approx(NOW + 300)
    assert parse_target("+2h", now=NOW) == pytest.approx(NOW + 7200)
    assert parse_target("+30", now=NOW) == pytest.approx(NOW + 30)


def test_parse_rejects_garbage():
    for bad in ["", "내일 2시", "25:99", "2026-13-45 99:99"]:
        with pytest.raises(TargetSpecError):
            parse_target(bad, now=NOW)


def test_parse_duration_units():
    assert parse_duration("150ms") == pytest.approx(0.15)
    assert parse_duration("2s") == pytest.approx(2.0)
    assert parse_duration("3") == pytest.approx(3.0)
    assert parse_duration("1.5m") == pytest.approx(90.0)
    with pytest.raises(TargetSpecError):
        parse_duration("빨리")


def test_format_epoch_renders_kst():
    assert format_epoch(kst(2026, 9, 15, 14, 0, 0)).startswith("2026-09-15 14:00:00.000")


def test_resync_checkpoints_are_descending_and_inside_window():
    points = resync_checkpoints(700.0, freeze_before=8.0)
    assert points == sorted(points, reverse=True)
    assert all(8.0 < p < 700.0 for p in points)
    assert 600.0 in points and 15.0 in points


def test_resync_checkpoints_skip_frozen_window():
    """마지막 freeze 구간 안에서는 재동기화를 예약하지 않는다."""
    points = resync_checkpoints(700.0, freeze_before=45.0)
    assert all(p > 45.0 for p in points)
    assert 30.0 not in points and 15.0 not in points


def test_resync_checkpoints_empty_when_target_is_close():
    assert resync_checkpoints(5.0, freeze_before=8.0) == []


def test_sleep_until_mono_is_accurate():
    target = time.monotonic() + 0.4
    report = sleep_until_mono(target)
    assert report.error >= 0  # 목표보다 일찍 깨어나면 안 된다
    assert report.error < 0.03  # 30ms 안쪽


def test_sleep_until_mono_returns_immediately_for_past_target():
    report = sleep_until_mono(time.monotonic() - 5.0)
    assert report.error >= 5.0


def test_sleep_until_mono_reports_progress():
    ticks = []
    sleep_until_mono(
        time.monotonic() + 0.35,
        on_tick=ticks.append,
        tick_interval=0.1,
    )
    assert len(ticks) >= 2
    assert ticks == sorted(ticks, reverse=True)  # 남은 시간은 줄어들어야 한다
