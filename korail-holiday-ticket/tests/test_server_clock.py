"""서버 시각 동기화 테스트."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_korail import FakeKorail  # noqa: E402
from korail_bot.server_clock import (  # noqa: E402
    ClockSyncError,
    DateSample,
    ServerClock,
    best_consistent_bounds,
    parse_http_date,
    sample_bounds,
)


def make_sample(lo: float, hi: float) -> DateSample:
    return DateSample(lo=lo, hi=hi, rtt=hi - lo, t0=0.0, t1=0.0)


def test_parse_http_date_is_utc():
    assert parse_http_date("Thu, 01 Jan 1970 00:00:00 GMT") == 0.0
    assert parse_http_date("Sun, 06 Sep 2026 12:00:00 GMT") == 1788696000.0


def test_parse_http_date_assumes_utc_when_timezone_missing():
    """타임존이 빠진 비정상 Date 헤더도 UTC 로 보고 처리한다."""
    assert parse_http_date("Sun, 06 Sep 2026 12:00:00") == 1788696000.0


def test_parse_http_date_rejects_garbage():
    with pytest.raises((TypeError, ValueError)):
        parse_http_date("어제 오후 세시")


def test_sample_bounds_brackets_offset():
    # 서버가 Date: 1000 을 내려줬고, 요청 0.10s 에 보내 0.20s 에 받았다면
    # 오프셋은 [1000 - 0.20, 1001 - 0.10) 안에 있어야 한다.
    lo, hi = sample_bounds(1000.0, 0.10, 0.20)
    assert lo == pytest.approx(999.80)
    assert hi == pytest.approx(1000.90)
    assert hi - lo == pytest.approx(1.0 + 0.10)  # 폭 = 1초 + 왕복지연


def test_sample_bounds_narrow_with_fast_response():
    """왕복지연이 짧을수록 구간이 좁아진다."""
    slow = sample_bounds(1000.0, 0.0, 0.5)
    fast = sample_bounds(1000.0, 0.0, 0.01)
    assert (fast[1] - fast[0]) < (slow[1] - slow[0])


def test_best_consistent_bounds_intersects_overlapping_samples():
    samples = [make_sample(9.5, 10.5), make_sample(9.9, 10.9), make_sample(9.7, 10.7)]
    lo, hi, used = best_consistent_bounds(samples)
    assert len(used) == 3
    assert (lo, hi) == pytest.approx((9.9, 10.5))


def test_best_consistent_bounds_rejects_outlier():
    """오염된 표본 하나가 결과를 망치지 않아야 한다."""
    good = [make_sample(9.9, 10.4), make_sample(9.95, 10.45), make_sample(10.0, 10.5)]
    outlier = make_sample(50.0, 51.0)  # 캐시된 응답 등으로 크게 어긋난 표본
    lo, hi, used = best_consistent_bounds(good + [outlier])
    assert len(used) == 3
    assert 10.0 <= lo < hi <= 10.4
    assert outlier not in used


def test_best_consistent_bounds_requires_samples():
    with pytest.raises(ClockSyncError):
        best_consistent_bounds([])


def test_sync_recovers_skew_from_live_server():
    """Date 헤더가 초 단위로 잘려 있어도 오프셋을 0.2초 안쪽으로 복원한다."""
    skew = 12.5
    with FakeKorail(skew=skew) as server:
        clock = ServerClock(server.url, samples=12, interval=0.1)
        try:
            estimate = clock.sync()
        finally:
            clock.close()

    assert estimate.offset == pytest.approx(skew, abs=0.2)
    assert estimate.uncertainty < 0.3
    assert estimate.used >= 8
    # 추정 구간이 실제 값을 실제로 품고 있어야 한다.
    assert abs(estimate.offset - skew) <= estimate.uncertainty + 0.05


def test_sync_recovers_negative_skew():
    """서버가 내 PC 보다 느린 경우도 동일하게 동작한다."""
    skew = -7.25
    with FakeKorail(skew=skew) as server:
        clock = ServerClock(server.url, samples=12, interval=0.1)
        try:
            estimate = clock.sync()
        finally:
            clock.close()
    assert estimate.offset == pytest.approx(skew, abs=0.2)


def test_estimate_projection_uses_monotonic_clock():
    with FakeKorail(skew=30.0) as server:
        clock = ServerClock(server.url, samples=6, interval=0.1)
        try:
            estimate = clock.sync()
        finally:
            clock.close()

    anchor = time.monotonic()
    later = estimate.server_time(anchor + 60.0)
    assert later - estimate.server_time(anchor) == pytest.approx(60.0)
    # 서버 시각 → monotonic 환산은 정확한 역함수여야 한다.
    assert estimate.mono_for(later) == pytest.approx(anchor + 60.0)


def test_sync_raises_when_server_unreachable():
    server = FakeKorail(skew=0.0).start()
    url = server.url
    server.stop()
    clock = ServerClock(url, samples=3, interval=0.01, timeout=1.0)
    try:
        with pytest.raises(ClockSyncError):
            clock.sync()
    finally:
        clock.close()
