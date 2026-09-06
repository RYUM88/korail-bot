"""코레일 서버 시각 동기화.

HTTP 응답의 ``Date`` 헤더로 서버 시각을 읽어, 내 PC 시계와의 오프셋을 추정한다.

``Date`` 헤더는 초 단위로 잘려 있어서(RFC 9110) 한 번만 읽으면 오차가 최대 1초다.
그래서 이 모듈은 표본 하나를 "값"이 아니라 "구간 제약"으로 다룬다.

    요청 전송 시각 t0, 응답 수신 시각 t1 (내 PC 시계), 응답의 Date 헤더 값 D 라면
    서버가 응답을 만든 실제 순간의 서버 시각은  [D, D+1)  안에 있고,
    그 순간의 내 PC 시각은  [t0, t1]  안에 있다. 따라서

        offset = (서버 시각 - 내 시각)  ∈  [D - t1,  D + 1 - t0)

표본을 여러 개 모아 이 구간들을 교집합하면 오프셋이 왕복지연(RTT) 수준까지 좁혀진다.
표본 하나가 오염(캐시 응답, 순간적인 지연 폭증)돼도 교집합이 비어버리지 않도록,
"가장 많은 구간이 겹치는 영역"을 고르는 방식으로 이상치를 걸러낸다.
"""

from __future__ import annotations

import http.client
import random
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "ClockEstimate",
    "ClockSyncError",
    "DateSample",
    "ServerClock",
    "best_consistent_bounds",
    "parse_http_date",
    "sample_bounds",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class ClockSyncError(RuntimeError):
    """서버 시각을 한 번도 읽지 못했을 때."""


def parse_http_date(value: str) -> float:
    """HTTP-date 문자열을 UTC 기준 epoch 초로 변환한다."""
    parsed = parsedate_to_datetime(value)
    if parsed is None:  # pragma: no cover - 파이썬 버전에 따라 예외를 던진다
        raise ValueError(f"Date 헤더를 해석할 수 없습니다: {value!r}")
    if parsed.tzinfo is None:  # HTTP-date 는 항상 GMT 지만 방어적으로 처리
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def sample_bounds(date_epoch: float, t0: float, t1: float) -> Tuple[float, float]:
    """표본 하나가 만드는 오프셋 구간 ``[lo, hi)`` 를 계산한다.

    Args:
        date_epoch: 응답 ``Date`` 헤더 값(초 단위로 잘린 서버 시각).
        t0: 요청을 보낸 시각(내 PC 시계).
        t1: 응답을 받은 시각(내 PC 시계).
    """
    return (date_epoch - t1, date_epoch + 1.0 - t0)


@dataclass(frozen=True)
class DateSample:
    """``Date`` 헤더 표본 하나."""

    lo: float
    hi: float
    rtt: float
    t0: float
    t1: float

    @property
    def width(self) -> float:
        return self.hi - self.lo


def best_consistent_bounds(
    samples: Sequence[DateSample],
) -> Tuple[float, float, List[DateSample]]:
    """가장 많은 표본이 동시에 만족하는 오프셋 구간을 찾는다.

    모든 구간의 교집합을 그대로 쓰면 표본 하나만 오염돼도 결과가 망가진다.
    대신 "겹치는 표본 수가 최대"인 영역을 고르고, 동점이면 더 좁은 쪽을 택한다.
    후보 시작점은 항상 어떤 구간의 좌측 끝이므로 그 점들만 확인하면 충분하다.

    Returns:
        ``(lo, hi, 사용된 표본들)``.

    Raises:
        ClockSyncError: 표본이 하나도 없을 때.
    """
    if not samples:
        raise ClockSyncError("서버 시각 표본이 없습니다.")

    best: Optional[Tuple[Tuple[int, float], float, float, List[DateSample]]] = None
    for candidate in samples:
        point = candidate.lo
        subset = [s for s in samples if s.lo <= point < s.hi]
        if not subset:  # pragma: no cover - lo < hi 이므로 자기 자신이 항상 포함된다
            continue
        lo = max(s.lo for s in subset)
        hi = min(s.hi for s in subset)
        key = (len(subset), -(hi - lo))
        if best is None or key > best[0]:
            best = (key, lo, hi, subset)

    assert best is not None
    _, lo, hi, subset = best
    return lo, hi, subset


@dataclass
class ClockEstimate:
    """어느 한 순간에 고정된 서버 시계 추정치.

    이후의 시각 계산은 모두 ``time.monotonic()`` 을 기준으로 한다.
    카운트다운 도중 OS 가 시스템 시계를 보정(NTP)해도 흔들리지 않기 위해서다.
    """

    offset: float
    """서버 시각 - 내 PC 시각 (초). 양수면 서버가 앞서 있다."""

    uncertainty: float
    """오프셋 추정 불확실성(초). 실제 오프셋은 ``offset ± uncertainty`` 안에 있다."""

    rtt: float
    """사용된 표본들의 최소 왕복지연(초)."""

    used: int
    """추정에 사용된 표본 수."""

    total: int
    """수집을 시도한 표본 수."""

    mono_at: float
    """추정을 확정한 순간의 ``time.monotonic()`` 값."""

    wall_at: float
    """추정을 확정한 순간의 ``time.time()`` 값."""

    def server_time(self, mono: Optional[float] = None) -> float:
        """주어진 monotonic 시점(기본값: 지금)의 서버 시각(epoch 초)."""
        if mono is None:
            mono = time.monotonic()
        return self.wall_at + self.offset + (mono - self.mono_at)

    def mono_for(self, server_epoch: float) -> float:
        """서버 시각 ``server_epoch`` 에 해당하는 monotonic 시점."""
        return self.mono_at + (server_epoch - (self.wall_at + self.offset))

    def describe(self) -> str:
        return (
            f"오프셋 {self.offset * 1000:+.0f}ms "
            f"(±{self.uncertainty * 1000:.0f}ms, RTT {self.rtt * 1000:.0f}ms, "
            f"표본 {self.used}/{self.total})"
        )


class ServerClock:
    """대상 서버에 ``HEAD`` 요청을 보내 서버 시각 오프셋을 추정한다.

    한 번 연 연결을 keep-alive 로 재사용하므로 표본당 왕복지연이 작고,
    동기화 1회에 보내는 요청은 ``samples`` 개(기본 12개)로 제한된다.
    """

    def __init__(
        self,
        url: str,
        *,
        samples: int = 12,
        interval: float = 0.12,
        timeout: float = 6.0,
        cache_buster: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        verify_tls: bool = True,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"http/https URL 이 필요합니다: {url!r}")
        self.scheme = parts.scheme
        self.host = parts.hostname or ""
        self.port = parts.port
        self.path = parts.path or "/"
        self.query = parts.query
        self.samples = max(3, samples)
        self.interval = interval
        self.timeout = timeout
        self.cache_buster = cache_buster
        self.user_agent = user_agent
        self.verify_tls = verify_tls
        self._conn: Optional[http.client.HTTPConnection] = None
        self._method = "HEAD"
        self.last_error: Optional[str] = None

    # -- 연결 관리 ---------------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is not None:
            return self._conn
        if self.scheme == "https":
            context = ssl.create_default_context()
            if not self.verify_tls:  # pragma: no cover - 진단용 탈출구
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self._conn = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout, context=context
            )
        else:
            self._conn = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _drop(self) -> None:
        self.close()

    # -- 표본 수집 ---------------------------------------------------------

    def _request_path(self) -> str:
        query = self.query
        if self.cache_buster:
            token = f"_={int(time.time() * 1000)}{random.randint(100, 999)}"
            query = f"{query}&{token}" if query else token
        return f"{self.path}?{query}" if query else self.path

    def _headers(self) -> dict:
        return {
            "Host": self.host,
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }

    def _one_sample(self) -> Optional[DateSample]:
        """표본 1개를 수집한다. 실패하거나 신뢰할 수 없으면 ``None``."""
        conn = self._connect()
        try:
            t0 = time.time()
            conn.request(self._method, self._request_path(), headers=self._headers())
            response = conn.getresponse()
            response.read()  # 연결 재사용을 위해 본문을 반드시 비운다
            t1 = time.time()
        except (http.client.HTTPException, socket.error, ssl.SSLError, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._drop()
            return None

        if response.status in (405, 501) and self._method == "HEAD":
            # HEAD 를 막아둔 서버라면 GET 으로 내려간다.
            self._method = "GET"
            self.last_error = "HEAD 미지원 → GET 으로 전환"
            return None

        date_header = response.getheader("Date")
        if not date_header:
            self.last_error = f"Date 헤더 없음 (status={response.status})"
            return None

        age = response.getheader("Age")
        if age is not None:
            try:
                if float(age) > 0:
                    # 캐시에서 나온 응답의 Date 는 과거 시각이므로 버린다.
                    self.last_error = f"캐시된 응답(Age={age}) 무시"
                    return None
            except ValueError:
                pass

        try:
            server_epoch = parse_http_date(date_header)
        except (TypeError, ValueError) as exc:
            self.last_error = f"Date 파싱 실패({date_header!r}): {exc}"
            return None

        lo, hi = sample_bounds(server_epoch, t0, t1)
        return DateSample(lo=lo, hi=hi, rtt=t1 - t0, t0=t0, t1=t1)

    # -- 공개 API ----------------------------------------------------------

    def sync(self) -> ClockEstimate:
        """표본을 모아 오프셋을 추정한다.

        Raises:
            ClockSyncError: 쓸 만한 표본을 하나도 얻지 못했을 때.
        """
        collected: List[DateSample] = []
        attempts = 0
        budget = self.samples + 3  # 재연결/GET 전환에 쓸 여유분
        while attempts < budget and len(collected) < self.samples:
            attempts += 1
            sample = self._one_sample()
            if sample is not None:
                collected.append(sample)
            if len(collected) < self.samples:
                # 표본이 1초 경계 여기저기에 흩어지도록 간격에 지터를 준다.
                time.sleep(self.interval * random.uniform(0.85, 1.15))

        if not collected:
            raise ClockSyncError(
                f"{self.host} 에서 서버 시각을 읽지 못했습니다. "
                f"마지막 오류: {self.last_error or '알 수 없음'}"
            )

        lo, hi, used = best_consistent_bounds(collected)
        mono_at = time.monotonic()
        wall_at = time.time()
        return ClockEstimate(
            offset=(lo + hi) / 2.0,
            uncertainty=(hi - lo) / 2.0,
            rtt=min(s.rtt for s in used),
            used=len(used),
            total=len(collected),
            mono_at=mono_at,
            wall_at=wall_at,
        )
