"""예매 시각 파싱과 정밀 대기.

카운트다운은 전부 ``time.monotonic()`` 위에서 돈다. 목표 시각은 "서버 시각"으로
주어지지만, 한 번 서버 시각을 monotonic 시점으로 환산해 두면 그 뒤로는 PC 시계가
NTP 로 보정되든 말든 영향을 받지 않는다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

__all__ = [
    "KST",
    "TargetSpecError",
    "format_epoch",
    "parse_duration",
    "parse_target",
    "resync_checkpoints",
    "sleep_until_mono",
]

KST = timezone(timedelta(hours=9), "KST")

_RELATIVE_RE = re.compile(r"^\+\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?$", re.IGNORECASE)
_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$")

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y%m%d %H%M%S",
    "%Y%m%d%H%M%S",
)

_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


class TargetSpecError(ValueError):
    """목표 시각 문자열을 해석할 수 없을 때."""


def parse_duration(text: str) -> float:
    """``"1.5s"``, ``"250ms"``, ``"2m"``, ``"3"``(초) 같은 표기를 초로 바꾼다."""
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*", text, re.IGNORECASE)
    if not match:
        raise TargetSpecError(f"시간 길이를 해석할 수 없습니다: {text!r}")
    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return value * _UNIT_SECONDS[unit]


def parse_target(spec: str, *, now: float, tz: timezone = KST) -> float:
    """목표 시각 문자열을 epoch 초로 바꾼다.

    받아들이는 형식:

    * ``"2026-09-15 14:00:00"`` — ``tz`` (기본 KST) 기준 절대 시각
    * ``"2026-09-15T14:00:00+09:00"`` — 타임존이 붙은 ISO 8601
    * ``"14:00:00"`` — 오늘의 그 시각. 이미 지났으면 내일로 넘어간다.
    * ``"+90s"`` / ``"+5m"`` — ``now`` 로부터의 상대 시각(리허설용)

    Args:
        spec: 목표 시각 문자열.
        now: 상대 시각과 "오늘"의 기준이 되는 서버 시각(epoch 초).
        tz: 타임존이 생략됐을 때 적용할 타임존.
    """
    text = spec.strip()
    if not text:
        raise TargetSpecError("목표 시각이 비어 있습니다.")

    relative = _RELATIVE_RE.match(text)
    if relative:
        amount = float(relative.group(1))
        unit = (relative.group(2) or "s").lower()
        return now + amount * _UNIT_SECONDS[unit]

    time_only = _TIME_ONLY_RE.match(text)
    if time_only:
        hour, minute = int(time_only.group(1)), int(time_only.group(2))
        second = float(time_only.group(3) or 0.0)
        base = datetime.fromtimestamp(now, tz)
        try:
            candidate = base.replace(
                hour=hour,
                minute=minute,
                second=int(second),
                microsecond=int(round((second % 1) * 1_000_000)),
            )
        except ValueError as exc:
            raise TargetSpecError(f"시각 범위를 벗어났습니다: {spec!r} ({exc})") from exc
        if candidate.timestamp() <= now:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _DATETIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise TargetSpecError(
            f"목표 시각을 해석할 수 없습니다: {spec!r} "
            "(예: '2026-09-15 14:00:00', '14:00', '+90s')"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.timestamp()


def format_epoch(epoch: float, tz: timezone = KST) -> str:
    """epoch 초를 ``2026-09-15 14:00:00.000 KST`` 형태로 출력한다."""
    moment = datetime.fromtimestamp(epoch, tz)
    label = moment.tzname() or moment.strftime("%z")
    return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d} {label}"


def resync_checkpoints(
    remaining: float,
    *,
    freeze_before: float,
    ladder: Optional[List[float]] = None,
) -> List[float]:
    """재동기화를 실행할 "목표까지 남은 시간(초)" 목록을 만든다.

    목표가 가까워질수록 촘촘하게 다시 맞추되, 마지막 ``freeze_before`` 초 안에서는
    네트워크를 건드리지 않는다. 결정적인 순간에 요청 지터가 끼어들지 않게 하려는 것.

    Args:
        remaining: 지금 기준으로 목표까지 남은 시간(초).
        freeze_before: 이 시간 이내로 들어오면 재동기화를 멈춘다.
        ladder: 사용할 체크포인트 사다리. 기본값은 10분~15초 구간.

    Returns:
        내림차순으로 정렬된 체크포인트 목록.
    """
    if ladder is None:
        ladder = [3600.0, 1800.0, 600.0, 300.0, 120.0, 60.0, 30.0, 15.0]
    picked = [c for c in ladder if freeze_before < c < remaining]
    return sorted(picked, reverse=True)


@dataclass
class WaitReport:
    """``sleep_until_mono`` 결과."""

    fired_at: float
    """실제로 깨어난 monotonic 시각."""

    error: float
    """목표 대비 오차(초). 양수면 늦게 깨어났다는 뜻."""


def sleep_until_mono(
    target_mono: float,
    *,
    spin: float = 0.02,
    on_tick: Optional[Callable[[float], None]] = None,
    tick_interval: float = 1.0,
) -> WaitReport:
    """monotonic 시각 ``target_mono`` 까지 정밀하게 대기한다.

    마지막 ``spin`` 초는 아주 짧은 sleep 으로 돌면서 OS 타이머 해상도(윈도우 기준
    최대 15ms)로 인한 오차를 줄인다.

    Args:
        target_mono: 깨어날 ``time.monotonic()`` 시각.
        spin: 마지막에 미세 루프로 대기할 구간의 길이(초).
        on_tick: 남은 시간(초)을 받아 진행 상황을 출력하는 콜백.
        tick_interval: ``on_tick`` 호출 간격(초).
    """
    next_tick = time.monotonic()
    while True:
        now = time.monotonic()
        remaining = target_mono - now
        if remaining <= spin:
            break
        if on_tick is not None and now >= next_tick:
            on_tick(remaining)
            next_tick = now + tick_interval
        time.sleep(min(remaining - spin, 0.2))

    while time.monotonic() < target_mono:
        time.sleep(0.0005)

    fired = time.monotonic()
    return WaitReport(fired_at=fired, error=fired - target_mono)
