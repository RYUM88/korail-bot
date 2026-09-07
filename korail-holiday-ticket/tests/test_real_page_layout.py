"""실제 bt.korail.com 레이아웃에서 올바른 카드를 고르는지 검증한다.

이 파일은 사용자가 보내 준 캡처(2026년 추석 예매 페이지)를 근거로 만들었다.
목표는 '추석 명절 승차권 / 예약만 가능' 카드이고, 페이지에는 그것과 헷갈리기 쉬운
링크가 둘 있다.

* '추석 승차권 예매 꿀팁(코레일과 SR이 하나로!)' — 유튜브 링크. 목표 카드보다 위에
  있으면서 '승차권 예매'를 그대로 품고 있다.
* '평상시 승차권 / 코레일 홈페이지로 이동' — 명절 예매가 아닌 일반 예매.

목표 카드에는 '예매'라는 단어가 아예 없다('예약만 가능'). 예매 계열 단어만 찾으면
목표를 놓치고 유튜브 링크를 누른다 — 실제로 그랬고, 이 테스트가 그 재발을 막는다.
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
from korail_bot.clicker import TicketPage  # noqa: E402
from korail_bot.scheduler import KST  # noqa: E402

playwright_api = pytest.importorskip("playwright.sync_api")

HOLIDAY_CARD = "추석 명절 승차권"
YOUTUBE_TIP = "꿀팁"
EVERYDAY_CARD = "평상시 승차권"


@pytest.fixture
def real_page(tmp_path):
    """실제 레이아웃을 띄운 가짜 코레일 + 브라우저."""
    launch_options = {"headless": True}
    executable = browser_executable()
    if executable:
        launch_options["executable_path"] = executable
    with FakeKorail(skew=0.0, layout="real") as server:
        with playwright_api.sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"), **launch_options
            )
            context.set_default_timeout(15000)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield server, context, page
            finally:
                context.close()


def test_holiday_card_is_chosen(real_page):
    """'예약만 가능' 이라 '예매'가 없어도 목표 카드를 찾아야 한다."""
    server, context, page = real_page
    ticket = TicketPage(context, page, server.url)
    ticket.open()
    page.wait_for_timeout(300)

    resolved = ticket.resolve()
    assert resolved is not None, "목표 카드를 찾지 못했습니다"
    _element, _frame, desc = resolved
    assert HOLIDAY_CARD in desc["label"]
    assert desc["href"] == "/reserve/holiday"


def test_youtube_tip_link_is_never_a_candidate(real_page):
    """'승차권 예매'를 품은 유튜브 링크에 낚이면 안 된다."""
    server, context, page = real_page
    ticket = TicketPage(context, page, server.url)
    ticket.open()
    page.wait_for_timeout(300)

    labels = [c.data["label"] for c in ticket.candidates()]
    assert not any(YOUTUBE_TIP in label for label in labels), labels
    assert not any("youtube" in (c.data.get("href") or "") for c in ticket.candidates())


def test_everyday_ticket_card_is_never_a_candidate(real_page):
    """'평상시 승차권'은 명절 예매가 아니므로 후보에서 빠져야 한다."""
    server, context, page = real_page
    ticket = TicketPage(context, page, server.url)
    ticket.open()
    page.wait_for_timeout(300)

    labels = [c.data["label"] for c in ticket.candidates()]
    assert not any(EVERYDAY_CARD in label for label in labels), labels


def test_holiday_card_outranks_everything_else(real_page):
    """목표 카드가 유일한 후보이거나, 최소한 압도적 1위여야 한다."""
    server, context, page = real_page
    ticket = TicketPage(context, page, server.url)
    ticket.open()
    page.wait_for_timeout(300)

    found = ticket.candidates()
    assert found, "후보가 하나도 없습니다"
    assert HOLIDAY_CARD in found[0].data["label"]
    if len(found) > 1:
        assert found[0].score >= found[1].score * 1.5


def test_text_selector_targets_holiday_card(real_page):
    """셀렉터를 직접 지정하는 경로도 목표 카드에 걸려야 한다."""
    server, context, page = real_page
    ticket = TicketPage(context, page, server.url, selector=f"text={HOLIDAY_CARD}")
    ticket.open()
    page.wait_for_timeout(300)

    resolved = ticket.resolve()
    assert resolved is not None
    element, _frame, _desc = resolved
    assert HOLIDAY_CARD in element.inner_text()


def test_run_opens_holiday_card_at_target_time(tmp_path, capsys):
    """전체 흐름: 지정한 서버 시각에 목표 카드를 눌러 예매 화면으로 넘어간다."""
    skew = 25.4
    with FakeKorail(skew=skew, layout="real") as server:
        target = math.ceil(server.server_now() + 7.0)
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
        opened = server.hits("/reserve/holiday")
        assert opened, "예매 화면으로 넘어가지 않았습니다"
        assert opened[0].server_time >= target - 0.25
        assert opened[0].server_time < target + 2.5

        # 함정들은 건드리지 않아야 한다.
        assert server.hits("/letskorail") == []

    assert "클릭 성공" in capsys.readouterr().out
