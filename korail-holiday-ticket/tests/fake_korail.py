"""테스트용 가짜 코레일 서버.

실제 bt.korail.com 에 붙지 않고도 전체 흐름을 검증하기 위한 것으로, 두 가지를
흉내 낸다.

* ``Date`` 헤더를 일부러 어긋난 시각으로 내려보낸다(서버 시계가 내 PC 와 다른 상황)
* 정해진 서버 시각이 되기 전에는 예매 버튼이 비활성 상태다
"""

from __future__ import annotations

import email.utils
import random
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

MAIN_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>명절 승차권 예매</title></head>
<body>
  <h1>추석 승차권 예매</h1>
  <ul>
    <li><a href="/notice">예매 안내</a></li>
    <li><a href="/mypage">예매확인/취소</a></li>
    <li><a href="/search">승차권 조회</a></li>
  </ul>
  <iframe src="/frame" width="800" height="400" title="예매영역"></iframe>
</body></html>
"""

FRAME_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"></head>
<body>
  <div class="wrap">
    <a id="btnReserve" class="btn disabled" href="javascript:void(0);"
       data-href="/booking">추석 승차권 예매</a>
    <a id="btnCheck" class="btn" href="/mypage">예매확인 / 취소</a>
  </div>
  <script>
    var OPEN_IN_MS = __OPEN_IN_MS__;
    var link = document.getElementById('btnReserve');
    function openReservation() {
      link.className = 'btn';
      link.setAttribute('href', link.getAttribute('data-href'));
    }
    if (OPEN_IN_MS <= 0) { openReservation(); }
    else { setTimeout(openReservation, OPEN_IN_MS); }
  </script>
</body></html>
"""

HOLIDAY_MAIN_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>추석 명절 승차권</title></head>
<body>
  <h1>풍요로운 한가위, 소중한 사람들과 함께</h1>
  <p><b>장애인, 경로, 임산부, 국가유공자(교통지원대상) 사전 예매(9시~15시)</b></p>
  <p>09월 03일(목) : 경부·경전·동해·중부내륙·경북·대구·충북·교외선</p>
  <p>09월 04일(금) : 호남·전라·중앙·강릉·장항·영동·태백·서해·경춘·목포보성선</p>
  <p><b>일반 회원 추석 승차권 예매(7시~13시)</b></p>
  <p>09월 07일(월) : <span class="tag">일반열차(ITX새마을·마음, 무궁화호)</span> 전체노선</p>
  <p>09월 08일(화) : <span class="tag">KTX서울청량리등출도착</span> 경전·강릉·동해·중앙·중부내륙선</p>
  <p>09월 11일(금) : <span class="tag">KTX서울출도착</span> 경부선</p>
  <a class="youtube" href="https://www.youtube.com/watch?v=xxx">추석 승차권 예매 꿀팁(코레일과 SR이 하나로!)</a>
  <div class="cards">
    <a class="card" href="/reserve/holiday">
      <strong>추석 명절 승차권</strong>
      <span>예약만 가능</span>
    </a>
    <a class="card" href="/letskorail">
      <strong>평상시 승차권</strong>
      <span>코레일 홈페이지로 이동</span>
    </a>
  </div>
  <p>※ 비회원 고객님은 회원가입 후 이용이 가능합니다.(SR단독회원 회원가입 필요)</p>
</body></html>
"""
"""실제 bt.korail.com 캡처의 텍스트 구성을 그대로 옮긴 것.

'추석 명절 승차권' 카드가 목표이고, 바로 위의 '추석 승차권 예매 꿀팁' 유튜브 링크와
옆의 '평상시 승차권' 카드가 함정이다."""

BOOKING_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>예매 진행</title></head>
<body><h1>예매 화면입니다</h1></body></html>
"""


@dataclass
class Hit:
    """서버가 받은 요청 하나."""

    path: str
    method: str
    server_time: float
    """서버(어긋난) 시계 기준 수신 시각."""


@dataclass
class FakeKorail:
    """스레드에서 도는 가짜 서버.

    Args:
        skew: 서버 시계가 테스트 프로세스 시계보다 앞선 정도(초).
        open_after: 서버 시작 시점 기준, 예매 버튼이 열리기까지의 시간(초).
            ``None`` 이면 처음부터 열려 있다.
        latency: 응답 전 인위적으로 넣을 지연 범위(초).
        layout: ``"simple"`` 은 iframe 과 비활성 버튼이 있는 단순 페이지,
            ``"real"`` 은 실제 bt.korail.com 캡처를 옮긴 페이지.
    """

    skew: float = 0.0
    open_after: Optional[float] = None
    layout: str = "simple"
    latency: Tuple[float, float] = (0.003, 0.030)
    _server: Optional[ThreadingHTTPServer] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _hits: List[Hit] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    open_epoch: Optional[float] = field(default=None, init=False)

    # -- 수명 주기 --------------------------------------------------------

    def start(self) -> "FakeKorail":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def date_time_string(self, timestamp=None):  # noqa: N802 - 상위 클래스 이름
                if timestamp is None:
                    timestamp = time.time() + outer.skew
                return email.utils.formatdate(timestamp, usegmt=True)

            def log_message(self, *args, **kwargs):  # noqa: D102 - 테스트 출력 억제
                pass

            def _delay(self):
                low, high = outer.latency
                if high > 0:
                    time.sleep(random.uniform(low, high))

            def _record(self):
                with outer._lock:
                    outer._hits.append(
                        Hit(
                            path=self.path.split("?")[0],
                            method=self.command,
                            server_time=time.time() + outer.skew,
                        )
                    )

            def _body_for(self, path: str) -> Tuple[int, bytes]:
                if path in ("/", "/holiday/main"):
                    if outer.layout == "real":
                        return 200, HOLIDAY_MAIN_HTML.encode("utf-8")
                    return 200, MAIN_HTML.encode("utf-8")
                if path in ("/reserve/holiday", "/letskorail"):
                    return 200, BOOKING_HTML.encode("utf-8")
                if path == "/frame":
                    if outer.open_epoch is None:
                        remaining = 0.0
                    else:
                        remaining = (
                            outer.open_epoch - (time.time() + outer.skew)
                        ) * 1000.0
                    html = FRAME_HTML.replace("__OPEN_IN_MS__", f"{remaining:.0f}")
                    return 200, html.encode("utf-8")
                if path == "/booking":
                    return 200, BOOKING_HTML.encode("utf-8")
                if path in ("/notice", "/mypage", "/search"):
                    return 200, b"<html><body>other</body></html>"
                return 404, b"<html><body>not found</body></html>"

            def do_GET(self):  # noqa: N802 - 상위 클래스 이름
                self._delay()
                self._record()
                status, body = self._body_for(self.path.split("?")[0])
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):  # noqa: N802 - 상위 클래스 이름
                self._delay()
                self._record()
                status, body = self._body_for(self.path.split("?")[0])
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        if self.open_after is not None:
            self.open_epoch = time.time() + self.skew + self.open_after
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeKorail":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # -- 조회 -------------------------------------------------------------

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/holiday/main"

    def hits(self, path: Optional[str] = None) -> List[Hit]:
        with self._lock:
            snapshot = list(self._hits)
        if path is None:
            return snapshot
        return [hit for hit in snapshot if hit.path == path]

    def server_now(self) -> float:
        """서버(어긋난) 시계의 현재 시각."""
        return time.time() + self.skew
