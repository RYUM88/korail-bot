"""버튼 탐색과 실제 클릭까지의 통합 테스트.

가짜 코레일 서버를 띄우고 진짜 크로미움으로 전체 흐름을 돌린다.
서버 시계는 일부러 어긋나 있고, 예매 버튼은 정해진 **서버 시각**이 되어야 열린다.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import browser_executable  # noqa: E402
from fake_korail import FakeKorail  # noqa: E402
from korail_bot.cli import main  # noqa: E402
from korail_bot.clicker import TicketPage, wait_until_closed  # noqa: E402
from korail_bot.scheduler import KST  # noqa: E402

playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def browser(tmp_path):
    """가짜 서버 하나와 영속 프로필 크로미움 하나."""
    launch_options = {"headless": True}
    executable = browser_executable()
    if executable:
        launch_options["executable_path"] = executable
    with playwright_api.sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / "profile"), **launch_options
        )
        context.set_default_timeout(15000)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def test_probe_prefers_reserve_button_inside_iframe(browser):
    """메인 프레임의 '예매 안내' 링크보다 iframe 안의 진짜 예매 버튼을 골라야 한다."""
    context, page = browser
    with FakeKorail(skew=0.0, open_after=0.0) as server:
        ticket = TicketPage(context, page, server.url)
        ticket.open()
        page.wait_for_timeout(500)

        found = ticket.candidates()
        labels = [c.data["label"] for c in found]
        assert "추석 승차권 예매" in labels

        best = found[0]
        assert best.data["label"] == "추석 승차권 예매"
        assert best.data["id"] == "btnReserve"
        assert best.frame_url.endswith("/frame")  # iframe 안에서 찾았다

        # '예매확인 / 취소' 는 제외 키워드에 걸려 후보에 없어야 한다.
        assert not any("예매확인" in label for label in labels)

        resolved = ticket.resolve()
        assert resolved is not None
        element, frame, desc = resolved
        assert desc["id"] == "btnReserve"


def test_disabled_button_is_skipped_until_it_opens(browser):
    """예매 시작 전에는 비활성 버튼을 클릭 대상으로 삼지 않는다."""
    context, page = browser
    with FakeKorail(skew=0.0, open_after=30.0) as server:
        ticket = TicketPage(context, page, server.url)
        ticket.open()
        page.wait_for_timeout(500)

        assert ticket.resolve(require_enabled=True) is None

        # 비활성 상태여도 후보 목록에는 잡히고, 상태가 표시되어야 한다.
        listed = ticket.candidates()
        reserve = next(c for c in listed if c.data["id"] == "btnReserve")
        assert reserve.data["enabled"] is False

        # --allow-disabled 에 해당하는 경로에서는 찾아진다.
        assert ticket.resolve(require_enabled=False) is not None


def test_dry_run_does_not_click(browser):
    context, page = browser
    with FakeKorail(skew=0.0, open_after=0.0) as server:
        ticket = TicketPage(context, page, server.url)
        ticket.open()
        page.wait_for_timeout(500)

        outcome = ticket.fire(dry_run=True, retry_window=3.0)
        assert outcome.dry_run is True
        assert outcome.clicked is False
        assert outcome.label == "추석 승차권 예매"
        assert server.hits("/booking") == []  # 실제로 눌리지 않았다


def test_run_clicks_at_korail_server_time(tmp_path, capsys):
    """전체 흐름: 어긋난 서버 시계에 맞춰 정확한 순간에 예매 버튼을 누른다."""
    skew = 43.7  # 서버가 내 PC 보다 43.7초 앞서 있다
    with FakeKorail(skew=skew, open_after=9.0) as server:
        # 예매 오픈 시각을 딱 떨어지는 초에 맞춘다(문자열로 넘기기 위해).
        target = math.ceil(server.open_epoch)
        server.open_epoch = target
        at = datetime.fromtimestamp(target, KST).strftime("%Y-%m-%d %H:%M:%S")

        exit_code = main(
            [
                "run",
                "--url", server.url,
                "--at", at,
                "--headless",
                "--no-keep-open",
                "--profile", str(tmp_path / "profile"),
                "--settle", "0.3",
                "--samples", "10",
                "--sample-interval", "0.1",
                "--reload-lead", "2s",
                "--freeze-before", "4s",
                "--after-wait", "1",
            ]
        )

        assert exit_code == 0
        bookings = server.hits("/booking")
        assert bookings, "예매 링크가 눌리지 않았습니다"

        clicked_at = bookings[0].server_time
        assert clicked_at >= target - 0.25, (
            f"예매 오픈({target}) 보다 {target - clicked_at:.2f}초 먼저 눌렸습니다"
        )
        assert clicked_at < target + 2.5, (
            f"예매 오픈보다 {clicked_at - target:.2f}초 늦게 눌렸습니다"
        )

        # 미끼 링크(예매확인/조회)는 건드리지 않아야 한다.
        assert server.hits("/mypage") == []
        assert server.hits("/search") == []

    output = capsys.readouterr().out
    assert "클릭 성공" in output
    assert "오프셋" in output


def test_run_rejects_past_target(tmp_path, capsys):
    with FakeKorail(skew=0.0) as server:
        exit_code = main(
            [
                "run",
                "--url", server.url,
                "--at", "2020-01-01 00:00:00",
                "--headless",
                "--no-keep-open",
                "--profile", str(tmp_path / "profile"),
                "--samples", "4",
            ]
        )
    assert exit_code == 2
    assert "이미 지났습니다" in capsys.readouterr().out


def test_clock_command_reports_offset(capsys):
    with FakeKorail(skew=-21.0) as server:
        exit_code = main(
            ["clock", "--url", server.url, "--samples", "10", "--sample-interval", "0.1"]
        )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "코레일 서버 시각" in output
    assert "-21" in output or "-20" in output


def test_wait_until_closed_returns_when_window_is_closed(browser):
    """Enter 입력 대신, 사용자가 창을 닫는 것으로 끝난다."""
    context, page = browser
    page.close()  # 사용자가 마지막 창을 닫은 상황
    assert wait_until_closed(context, timeout=5.0, poll=0.1) is True


def test_wait_until_closed_gives_up_after_timeout(browser):
    context, _page = browser
    assert wait_until_closed(context, timeout=0.4, poll=0.1) is False


def test_login_refuses_headless(capsys):
    """login 은 사람이 보는 창이 필요하다."""
    assert main(["login", "--headless"]) == 2
    assert "창이 필요합니다" in capsys.readouterr().out
