"""명령줄 인터페이스.

세 개의 하위 명령을 제공한다.

* ``clock`` — 코레일 서버 시각과 내 PC 시계의 차이만 확인
* ``probe`` — 페이지를 열어 클릭 후보 버튼을 나열(예매일 전에 셀렉터를 고정할 때)
* ``run``   — 지정한 서버 시각에 예매 버튼을 클릭
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

from .clicker import (
    DEFAULT_EXCLUDE_KEYWORDS,
    DEFAULT_INCLUDE_KEYWORDS,
    DEFAULT_MIN_SCORE,
    TicketPage,
    wait_until_closed,
)
from .scheduler import (
    KST,
    TargetSpecError,
    format_epoch,
    parse_duration,
    parse_target,
    resync_checkpoints,
    sleep_until_mono,
)
from .server_clock import ClockSyncError, ServerClock

DEFAULT_URL = "https://bt.korail.com/holiday/main#none"
DEFAULT_PROFILE = ".profile"


# ---------------------------------------------------------------- 출력 도우미


def log(message: str = "") -> None:
    print(message, flush=True)


def humanize(seconds: float) -> str:
    """남은 시간을 ``3일 02:15:07`` 형태로."""
    negative = seconds < 0
    seconds = abs(seconds)
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    if days:
        text = f"{days}일 {text}"
    return f"-{text}" if negative else text


def split_keywords(value: Optional[str], fallback: Sequence[str]) -> List[str]:
    if value is None:
        return list(fallback)
    return [part.strip() for part in value.split(",") if part.strip()]


# ---------------------------------------------------------------- 브라우저


def launch_browser(playwright, args):
    """영속 프로필로 크로미움을 띄운다.

    프로필을 유지하는 이유: 예매일 전에 미리 로그인해 두면 그 세션이 그대로 남는다.
    """
    profile = os.path.abspath(args.profile)
    os.makedirs(profile, exist_ok=True)
    options: Dict[str, Any] = {
        "headless": args.headless,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
    }
    if not args.headless:
        options["no_viewport"] = True
        options["args"] = ["--start-maximized"]
    if args.channel:
        options["channel"] = args.channel
    if args.browser_path:
        options["executable_path"] = args.browser_path

    context = playwright.chromium.launch_persistent_context(profile, **options)
    context.set_default_timeout(15000)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def build_page(context, page, args) -> TicketPage:
    return TicketPage(
        context,
        page,
        args.url,
        selector=args.selector,
        include=split_keywords(args.include, DEFAULT_INCLUDE_KEYWORDS),
        exclude=split_keywords(args.exclude, DEFAULT_EXCLUDE_KEYWORDS),
        min_score=args.min_score,
        log=log,
    )


def make_clock(args) -> ServerClock:
    return ServerClock(
        args.time_url or args.url,
        samples=args.samples,
        interval=args.sample_interval,
        timeout=args.http_timeout,
    )


def sync_clock(clock: ServerClock, *, label: str = "서버 시각 동기화"):
    estimate = clock.sync()
    log(
        f"  {label}: {format_epoch(estimate.server_time())}  |  {estimate.describe()}"
    )
    return estimate


# ---------------------------------------------------------------- clock


def cmd_clock(args) -> int:
    clock = make_clock(args)
    rounds = max(1, args.rounds)
    try:
        for index in range(rounds):
            estimate = clock.sync()
            server_now = estimate.server_time()
            log(f"[{index + 1}/{rounds}] 코레일 서버 시각 : {format_epoch(server_now)}")
            log(f"          내 PC 시계     : {format_epoch(time.time())}")
            log(f"          차이           : {estimate.describe()}")
            if estimate.uncertainty > 0.25:
                log(
                    "          ! 불확실성이 큽니다. 네트워크가 불안정하거나 캐시가 끼어 "
                    "있을 수 있습니다."
                )
            if index + 1 < rounds:
                time.sleep(max(0.0, args.rounds_interval))
    except ClockSyncError as exc:
        log(f"오류: {exc}")
        return 1
    finally:
        clock.close()
    return 0


# ---------------------------------------------------------------- login


def cmd_login(args) -> int:
    """브라우저를 열어 두고 사용자가 로그인하도록 한 뒤 프로필에 저장한다.

    예매 당일에는 대기 중에 손댈 일이 없어야 하므로, 로그인은 미리 끝내 둔다.
    프로필 폴더에 세션이 남아 ``run`` 이 그대로 이어받는다.
    """
    from playwright.sync_api import sync_playwright

    if args.headless:
        log("오류: login 은 창이 필요합니다. --headless 를 빼고 실행하세요.")
        return 2

    with sync_playwright() as playwright:
        context, page = launch_browser(playwright, args)
        try:
            log(f"페이지를 엽니다: {args.url}")
            try:
                page.goto(args.url, timeout=args.nav_timeout * 1000,
                          wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001 - 사용자가 직접 이동해도 된다
                log(f"  ! 로딩 실패: {type(exc).__name__}: {exc}")
            log("")
            log("브라우저에서 코레일에 로그인하세요.")
            log(f"  로그인 상태는 프로필 폴더({os.path.abspath(args.profile)})에 저장됩니다.")
            log("  다 하셨으면 브라우저 창을 닫으세요.")
            finished = wait_until_closed(context, timeout=args.keep_open_timeout)
            log("")
            if finished:
                log("저장했습니다. 이제 run 명령이 이 로그인 상태를 그대로 씁니다.")
            else:
                log(f"제한 시간({args.keep_open_timeout:.0f}초)이 지나 종료합니다.")
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001 - 이미 사용자가 닫았다
                pass
    return 0


# ---------------------------------------------------------------- probe


def cmd_probe(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context, page = launch_browser(playwright, args)
        ticket = build_page(context, page, args)
        try:
            log(f"페이지를 엽니다: {args.url}")
            ticket.open(timeout=args.nav_timeout)
            if args.settle > 0:
                time.sleep(args.settle)

            found = ticket.candidates()
            log("")
            if not found:
                log("클릭 후보를 찾지 못했습니다.")
                log(
                    "  · 예매 기간이 아니면 버튼이 없을 수 있습니다.\n"
                    "  · --include 로 키워드를 바꾸거나, 브라우저에서 직접 확인 후 "
                    "--selector 로 지정하세요."
                )
            else:
                log(f"클릭 후보 {len(found)}개 (점수 높은 순):")
                for index, candidate in enumerate(found[: args.limit], start=1):
                    log(f"\n  [{index}] frame: {candidate.frame_url}")
                    log(f"      {candidate.summary()}")

            log("")
            log("프레임 목록:")
            for frame in page.frames:
                log(f"  - {frame.url}")

            if args.out:
                payload = {
                    "url": args.url,
                    "captured_at": format_epoch(time.time()),
                    "frames": [f.url for f in page.frames],
                    "candidates": [
                        {"frame": c.frame_url, **c.data} for c in found
                    ],
                }
                with open(args.out, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                log(f"\n결과를 저장했습니다: {args.out}")

            if found:
                best = found[0]
                log("")
                highlighted = ticket.highlight_best()
                if highlighted is None:
                    log("! 자동 클릭 대상이 없습니다.")
                    if args.selector:
                        log(f"  --selector \"{args.selector}\" 에 맞는 요소를 찾지 못했습니다.")
                    else:
                        log(
                            f"  최고점 후보({best.data.get('score')})가 "
                            f"--min-score({args.min_score:.0f}) 에 못 미칩니다."
                        )
                        log("  아래 셀렉터를 --selector 로 직접 지정하는 것을 권합니다.")
                else:
                    log("실제로 눌릴 요소를 브라우저에서 빨간 테두리로 표시했습니다.")
                    log("  그게 진짜 예매 버튼이 맞는지 눈으로 확인하세요.")
                log("")
                log("예매 당일에는 아래 셀렉터를 그대로 쓰면 확실합니다:")
                log(f"  --selector \"{best.data.get('cssPath')}\"")

            if args.keep_open and not args.headless:
                log("")
                log("확인이 끝나면 브라우저 창을 닫으세요. (Ctrl+C 로도 종료)")
                wait_until_closed(context, timeout=args.keep_open_timeout)
        finally:
            context.close()
    return 0


# ---------------------------------------------------------------- run


def cmd_run(args) -> int:
    from playwright.sync_api import sync_playwright

    if not args.at:
        log("오류: 목표 시각이 없습니다.")
        log("  --at 으로 넘기거나, 설정 파일에 \"at\" 을 넣으세요.")
        log("  예: python -m korail_bot run --at \"2026-09-15 14:00:00\"")
        return 2

    try:
        lead = parse_duration(args.lead)
        freeze_before = parse_duration(args.freeze_before)
        reload_lead = parse_duration(args.reload_lead)
    except TargetSpecError as exc:
        log(f"오류: {exc}")
        return 2

    clock = make_clock(args)

    log("=" * 72)
    log(" 코레일 명절 승차권 예매 버튼 자동 클릭")
    log("=" * 72)
    log("")
    log("[1/5] 코레일 서버 시각을 확인합니다")
    try:
        estimate = sync_clock(clock)
    except ClockSyncError as exc:
        log(f"  오류: {exc}")
        log("  네트워크 연결과 --time-url 을 확인하세요.")
        clock.close()
        return 1

    try:
        target = parse_target(args.at, now=estimate.server_time(), tz=KST)
    except TargetSpecError as exc:
        log(f"  오류: {exc}")
        clock.close()
        return 2

    remaining = target - estimate.server_time()
    if remaining <= 0:
        log(f"  오류: 목표 시각이 이미 지났습니다 ({format_epoch(target)})")
        clock.close()
        return 2

    log("")
    log("[2/5] 목표 시각")
    log(f"  코레일 서버 기준 : {format_epoch(target)}")
    log(f"  내 PC 시계 기준  : {format_epoch(target - estimate.offset)}")
    if abs(lead) > 0:
        log(f"  선행 클릭 보정   : {lead * 1000:+.0f}ms (이만큼 먼저 누름)")
    log(f"  남은 시간        : {humanize(remaining)}")
    if args.dry_run:
        log("  ** 리허설 모드: 실제로 클릭하지 않습니다 **")

    with sync_playwright() as playwright:
        context, page = launch_browser(playwright, args)
        ticket = build_page(context, page, args)
        try:
            log("")
            log("[3/5] 예매 페이지를 미리 열어 둡니다")
            log(f"  {args.url}")
            try:
                ticket.open(timeout=args.nav_timeout)
            except Exception as exc:  # noqa: BLE001 - 나중에 새로고침으로 회복 가능
                log(f"  ! 첫 로딩 실패: {type(exc).__name__}: {exc}")
            if args.settle > 0:
                time.sleep(args.settle)

            if args.selector:
                log(f"  클릭 대상(셀렉터 지정): {args.selector}")
                if ticket.resolve(require_enabled=False, require_visible=False) is None:
                    log("  ! 지금은 해당 셀렉터를 찾을 수 없습니다(예매 시작 후 생길 수 있음)")
            else:
                preview = ticket.candidates()
                if preview:
                    best = preview[0]
                    log(f"  현재 시점 최유력 후보: '{best.data.get('label')}' "
                        f"({best.data.get('cssPath')})")
                    if not best.data.get("enabled"):
                        log("    (지금은 비활성 상태 — 예매 시작 시각에 열릴 것으로 보입니다)")
                    if best.score < args.min_score:
                        log(
                            f"  ! 이 후보의 점수({best.data.get('score')})가 "
                            f"--min-score({args.min_score:.0f}) 에 못 미쳐 "
                            "지금 상태로는 클릭하지 않습니다."
                        )
                        log("    probe 로 확인한 뒤 --selector 로 지정하시길 권합니다.")
                else:
                    log("  현재는 예매 버튼이 보이지 않습니다. 시작 직전 새로고침 후 다시 찾습니다.")

            if args.login_pause and not args.headless:
                log("")
                log("  ! --login-pause 는 터미널에서 Enter 입력을 기다립니다.")
                log("    로그인은 미리 'login' 명령으로 해 두는 편이 낫습니다.")
                try:
                    input("  준비를 마친 뒤 Enter 를 누르세요... ")
                except (EOFError, OSError):
                    log("    (표준입력이 없어 그냥 진행합니다)")

            log("")
            log("[4/5] 대기 (Ctrl+C 로 중단)")
            fire_mono = estimate.mono_for(target) - lead

            checkpoints = resync_checkpoints(
                target - estimate.server_time(), freeze_before=freeze_before
            )
            for checkpoint in checkpoints:
                wake_mono = estimate.mono_for(target - checkpoint)
                if wake_mono <= time.monotonic():
                    continue
                sleep_until_mono(
                    wake_mono,
                    on_tick=lambda left, cp=checkpoint: print(
                        f"\r  남은 시간 {humanize(fire_mono - time.monotonic())}"
                        f"  (다음 재동기화까지 {left:,.0f}s)   ",
                        end="",
                        flush=True,
                    ),
                )
                print("\r" + " " * 78 + "\r", end="", flush=True)
                try:
                    estimate = sync_clock(clock, label="재동기화")
                    fire_mono = estimate.mono_for(target) - lead
                except ClockSyncError as exc:
                    log(f"  ! 재동기화 실패({exc}) — 직전 추정치를 계속 사용합니다")

            clock.close()

            if reload_lead > 0:
                reload_mono = fire_mono - reload_lead
                if reload_mono > time.monotonic():
                    sleep_until_mono(
                        reload_mono,
                        on_tick=lambda left: print(
                            f"\r  남은 시간 {humanize(fire_mono - time.monotonic())}   ",
                            end="",
                            flush=True,
                        ),
                    )
                    print("\r" + " " * 78 + "\r", end="", flush=True)
                    log(f"  · 클릭 {reload_lead:.1f}초 전 — 페이지를 새로고침합니다")
                    ticket.reload(timeout=args.nav_timeout)

            wait_report = sleep_until_mono(
                fire_mono,
                on_tick=lambda left: print(
                    f"\r  남은 시간 {humanize(left)}   ", end="", flush=True
                ),
            )
            print("\r" + " " * 78 + "\r", end="", flush=True)

            log("")
            log("[5/5] 클릭!")
            fired_server_time = estimate.server_time(wait_report.fired_at)
            log(f"  발사 시각(서버 기준) : {format_epoch(fired_server_time)}")
            log(f"  타이머 오차          : {wait_report.error * 1000:+.1f}ms")

            outcome = ticket.fire(
                mode=args.click_mode,
                retry_window=args.retry_window,
                retry_interval=args.retry_interval,
                fast_interval=args.fast_retry_interval,
                fast_window=args.fast_retry_window,
                reload_after=args.reload_after,
                require_enabled=not args.allow_disabled,
                dry_run=args.dry_run,
            )

            log("")
            if outcome.dry_run:
                if outcome.label:
                    log(f"  [리허설] 눌렀을 대상: '{outcome.label}'")
                    log("  브라우저에서 빨간 테두리로 표시했습니다.")
                else:
                    log("  [리허설] 클릭 대상을 찾지 못했습니다.")
            elif outcome.clicked:
                clicked_server = estimate.server_time(outcome.clicked_at_mono)
                log(f"  ✔ 클릭 성공 ({outcome.mode}, 시도 {outcome.attempts}회)")
                log(f"    대상        : '{outcome.label}'")
                log(f"    클릭 시각   : {format_epoch(clicked_server)} (서버 기준)")
                settled = ticket.settle(wait=args.after_wait)
                if settled.new_pages:
                    for url in settled.new_pages:
                        log(f"    새 창       : {url}")
                log(f"    현재 주소   : {settled.final_url}")
                for url in settled.frame_urls:
                    log(f"    프레임      : {url}")
            else:
                log(f"  ✘ 클릭하지 못했습니다 (시도 {outcome.attempts}회)")
                for message in outcome.errors[-5:]:
                    log(f"    - {message}")
                log(
                    "    probe 명령으로 셀렉터를 확인한 뒤 --selector 로 지정하면 "
                    "확실합니다."
                )

            log("")
            log("  이후 로그인·좌석 선택·결제는 열려 있는 브라우저에서 직접 진행하세요.")

            if args.keep_open and not args.headless:
                log("  브라우저 창을 닫으면 종료됩니다. (Ctrl+C 로도 종료)")
                wait_until_closed(context, timeout=args.keep_open_timeout)
            elif args.keep_open:
                time.sleep(args.after_wait)
        except KeyboardInterrupt:
            log("\n중단했습니다.")
            return 130
        finally:
            clock.close()
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
    return 0


# ---------------------------------------------------------------- argparse


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=DEFAULT_URL, help="예매 페이지 주소")
    parser.add_argument(
        "--time-url",
        default=None,
        help="서버 시각을 읽을 주소 (기본값: --url 과 동일)",
    )
    parser.add_argument("--selector", default=None, help="클릭할 요소의 CSS/XPath 셀렉터")
    parser.add_argument(
        "--include",
        default=None,
        help=f"버튼 탐색 키워드(쉼표 구분, 앞쪽이 우선). 기본값: "
        f"{','.join(DEFAULT_INCLUDE_KEYWORDS)}",
    )
    parser.add_argument(
        "--exclude",
        default=None,
        help=f"제외 키워드(쉼표 구분). 기본값: {','.join(DEFAULT_EXCLUDE_KEYWORDS)}",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="브라우저 프로필 폴더")
    parser.add_argument("--headless", action="store_true", help="창 없이 실행")
    parser.add_argument(
        "--channel",
        default=os.environ.get("KORAIL_BOT_BROWSER_CHANNEL") or None,
        help="'chrome' 등 설치된 브라우저 채널 (환경변수 KORAIL_BOT_BROWSER_CHANNEL)",
    )
    parser.add_argument(
        "--browser-path",
        default=os.environ.get("KORAIL_BOT_BROWSER_PATH") or None,
        help="브라우저 실행 파일 경로 (환경변수 KORAIL_BOT_BROWSER_PATH)",
    )
    parser.add_argument(
        "--nav-timeout", type=float, default=60.0, help="페이지 로딩 제한 시간(초)"
    )
    parser.add_argument(
        "--settle", type=float, default=1.5, help="로딩 후 추가 대기(초)"
    )
    parser.add_argument("--samples", type=int, default=12, help="시각 동기화 표본 수")
    parser.add_argument(
        "--sample-interval", type=float, default=0.12, help="표본 간격(초)"
    )
    parser.add_argument(
        "--http-timeout", type=float, default=6.0, help="시각 동기화 요청 제한 시간(초)"
    )
    parser.add_argument(
        "--keep-open-timeout",
        type=float,
        default=3600.0,
        help="브라우저가 닫히기를 기다리는 최대 시간(초)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=f"자동 탐색으로 클릭할 최소 점수 (기본 {DEFAULT_MIN_SCORE:.0f}). "
        "낮추면 애매한 후보도 누르게 되므로 권장하지 않습니다.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="korail_bot",
        description="코레일 명절 승차권 예매 버튼을 서버 시각에 맞춰 클릭합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python -m korail_bot clock\n"
            "  python -m korail_bot login\n"
            "  python -m korail_bot probe --out candidates.json\n"
            "  python -m korail_bot run --at '2026-09-15 14:00:00' --dry-run\n"
            "  python -m korail_bot run --at '2026-09-15 14:00:00' "
            "--selector 'a.btn_reserve' --lead 150ms\n"
        ),
    )
    parser.add_argument("--config", default=None, help="기본값을 담은 JSON 설정 파일")
    sub = parser.add_subparsers(dest="command", required=True)

    clock_parser = sub.add_parser("clock", help="서버 시각과 내 PC 시계 차이 확인")
    add_common(clock_parser)
    clock_parser.add_argument("--rounds", type=int, default=1, help="반복 횟수")
    clock_parser.add_argument(
        "--rounds-interval", type=float, default=5.0, help="반복 간격(초)"
    )
    clock_parser.set_defaults(func=cmd_clock)

    login_parser = sub.add_parser("login", help="미리 로그인해서 프로필에 저장")
    add_common(login_parser)
    login_parser.set_defaults(func=cmd_login)

    probe_parser = sub.add_parser("probe", help="예매 버튼 후보 탐색")
    add_common(probe_parser)
    probe_parser.add_argument("--limit", type=int, default=15, help="출력할 후보 수")
    probe_parser.add_argument("--out", default=None, help="결과를 저장할 JSON 파일")
    probe_parser.add_argument(
        "--keep-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="확인이 끝날 때까지 브라우저를 열어 둠",
    )
    probe_parser.set_defaults(func=cmd_probe)

    run_parser = sub.add_parser("run", help="지정한 서버 시각에 예매 버튼 클릭")
    add_common(run_parser)
    run_parser.add_argument(
        "--at",
        default=None,
        help="목표 시각(코레일 서버 기준). "
        "'2026-09-15 14:00:00' / '14:00' / '+90s' 형식. "
        "설정 파일의 at 값으로도 지정할 수 있습니다.",
    )
    run_parser.add_argument(
        "--lead", default="0ms", help="목표보다 이만큼 먼저 클릭 (예: 150ms)"
    )
    run_parser.add_argument(
        "--freeze-before",
        default="8s",
        help="목표 직전 이 시간 안에서는 재동기화를 하지 않음",
    )
    run_parser.add_argument(
        "--reload-lead",
        default="3s",
        help="클릭 이 시간 전에 페이지 새로고침 (0 이면 하지 않음)",
    )
    run_parser.add_argument(
        "--click-mode",
        choices=("auto", "real", "dom", "dispatch"),
        default="auto",
        help="클릭 방식. auto/real 은 실제 마우스 이벤트(가장 확실, 약 130ms 소요), "
        "dom 은 el.click() 직접 호출(약 60ms 더 빠르지만 합성 이벤트)",
    )
    run_parser.add_argument(
        "--retry-window", type=float, default=20.0, help="클릭 재시도 제한 시간(초)"
    )
    run_parser.add_argument(
        "--retry-interval", type=float, default=0.3, help="클릭 재시도 간격(초)"
    )
    run_parser.add_argument(
        "--fast-retry-interval",
        type=float,
        default=0.02,
        help="예매 시작 직후 촘촘한 재시도 간격(초)",
    )
    run_parser.add_argument(
        "--fast-retry-window",
        type=float,
        default=2.0,
        help="촘촘한 재시도를 유지할 시간(초)",
    )
    run_parser.add_argument(
        "--reload-after",
        type=float,
        default=1.0,
        help="버튼을 못 찾은 채 이만큼 지나면 새로고침(0이면 안 함). "
        "코레일이 예매 시작 시각에 버튼을 새로 내려주는 경우를 위한 것입니다.",
    )
    run_parser.add_argument(
        "--allow-disabled", action="store_true", help="비활성 상태여도 클릭 시도"
    )
    run_parser.add_argument(
        "--after-wait", type=float, default=5.0, help="클릭 후 결과 확인 대기(초)"
    )
    run_parser.add_argument(
        "--login-pause",
        action="store_true",
        help="대기 시작 전에 멈춰서 로그인할 시간을 줌",
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", help="실제로 누르지 않고 대상만 표시"
    )
    run_parser.add_argument(
        "--keep-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="클릭 후 브라우저를 열어 둠",
    )
    run_parser.set_defaults(func=cmd_run)

    # 설정 파일의 값을 적용할 때 "사용자가 직접 준 값"과 "기본값"을 구분하려면
    # 하위 명령별 기본값을 알아야 한다. 최상위 파서에는 그 정보가 없다.
    parser.korail_subparsers = {
        "clock": clock_parser,
        "login": login_parser,
        "probe": probe_parser,
        "run": run_parser,
    }
    return parser


def config_default(
    parser: argparse.ArgumentParser,
    subparser: Optional[argparse.ArgumentParser],
    key: str,
):
    """``key`` 의 기본값을 하위 파서 → 최상위 파서 순으로 찾는다."""
    if subparser is not None:
        value = subparser.get_default(key)
        if value is not None:
            return value
    return parser.get_default(key)


def apply_config(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    """``--config`` JSON 파일의 값을 기본값으로 깔고 CLI 인자로 덮어쓴다."""
    args = parser.parse_args(argv)
    if not args.config:
        return args
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    subparser = getattr(parser, "korail_subparsers", {}).get(args.command)
    for key, value in config.items():
        key = key.replace("-", "_")
        if key in ("config", "command", "func") or not hasattr(args, key):
            continue
        # 사용자가 명령줄에서 직접 준 값이 항상 이긴다.
        if getattr(args, key) == config_default(parser, subparser, key):
            setattr(args, key, value)
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = apply_config(parser, list(sys.argv[1:] if argv is None else argv))
    return args.func(args)
