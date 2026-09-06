"""CLI 인자 파싱과 설정 파일 처리 테스트."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_korail import FakeKorail  # noqa: E402
from korail_bot.cli import apply_config, build_parser, humanize, split_keywords  # noqa: E402
from korail_bot.clicker import DEFAULT_INCLUDE_KEYWORDS  # noqa: E402


@pytest.fixture
def config_file(tmp_path):
    def write(payload: dict) -> str:
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    return write


def parse(argv):
    return apply_config(build_parser(), argv)


def test_subcommand_defaults_are_used_without_config():
    args = parse(["run", "--at", "14:00"])
    assert args.lead == "0ms"
    assert args.reload_lead == "3s"
    assert args.click_mode == "auto"
    assert args.selector is None


def test_config_file_overrides_subcommand_defaults(config_file):
    """하위 명령의 기본값도 설정 파일로 덮을 수 있어야 한다.

    예매 당일 설정이 조용히 무시되면 치명적이므로 회귀를 막는다.
    """
    path = config_file(
        {
            "lead": "250ms",
            "selector": "#btnReserve",
            "reload_lead": "5s",
            "min_score": 42.0,
            "click_mode": "dom",
            "reload_after": 2.0,
        }
    )
    args = parse(["--config", path, "run", "--at", "14:00"])
    assert args.lead == "250ms"
    assert args.selector == "#btnReserve"
    assert args.reload_lead == "5s"
    assert args.min_score == 42.0
    assert args.click_mode == "dom"
    assert args.reload_after == 2.0


def test_command_line_beats_config_file(config_file):
    path = config_file({"lead": "250ms", "selector": "#fromConfig"})
    args = parse(
        ["--config", path, "run", "--at", "14:00", "--lead", "999ms",
         "--selector", "#fromCli"]
    )
    assert args.lead == "999ms"
    assert args.selector == "#fromCli"


def test_config_file_cannot_hijack_internal_fields(config_file):
    """``func``/``command`` 같은 내부 필드는 설정 파일이 건드릴 수 없어야 한다."""
    path = config_file({"func": "evil", "command": "clock", "config": "other.json"})
    args = parse(["--config", path, "run", "--at", "14:00"])
    assert args.command == "run"
    assert callable(args.func)
    assert args.config == path


def test_config_file_ignores_unknown_keys(config_file):
    path = config_file({"완전히_없는_옵션": 1, "lead": "10ms"})
    args = parse(["--config", path, "run", "--at", "14:00"])
    assert args.lead == "10ms"


def test_config_applies_to_probe_command(config_file):
    path = config_file({"limit": 3, "url": "https://example.invalid/"})
    args = parse(["--config", path, "probe"])
    assert args.limit == 3
    assert args.url == "https://example.invalid/"


def test_target_time_can_come_from_config(config_file):
    """예매 시각을 설정 파일에 굳혀 두고 인자 없이 실행할 수 있어야 한다."""
    path = config_file({"at": "2026-09-15 14:00:00"})
    args = parse(["--config", path, "run"])
    assert args.at == "2026-09-15 14:00:00"


def test_run_without_target_time_fails_clearly(capsys):
    """--at 도 설정 파일도 없으면 조용히 죽지 말고 이유를 말해야 한다."""
    from korail_bot.cli import main

    assert main(["run"]) == 2
    assert "목표 시각이 없습니다" in capsys.readouterr().out


def test_split_keywords_falls_back_to_defaults():
    assert split_keywords(None, DEFAULT_INCLUDE_KEYWORDS) == list(DEFAULT_INCLUDE_KEYWORDS)
    assert split_keywords("가, 나 ,다", DEFAULT_INCLUDE_KEYWORDS) == ["가", "나", "다"]
    assert split_keywords(",,", DEFAULT_INCLUDE_KEYWORDS) == []


def test_humanize_formats_remaining_time():
    assert humanize(0) == "00:00:00"
    assert humanize(61) == "00:01:01"
    assert humanize(3661) == "01:01:01"
    assert humanize(90061) == "1일 01:01:01"
    assert humanize(-5) == "-00:00:05"


def test_probe_command_runs_end_to_end(tmp_path, capsys):
    out = tmp_path / "candidates.json"
    with FakeKorail(skew=0.0, open_after=0.0) as server:
        from korail_bot.cli import main

        exit_code = main(
            [
                "probe",
                "--url", server.url,
                "--headless",
                "--profile", str(tmp_path / "profile"),
                "--settle", "0.3",
                "--out", str(out),
            ]
        )
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "추석 승차권 예매" in printed
    assert "--selector" in printed  # 그대로 복사해 쓸 수 있게 안내한다

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["candidates"][0]["id"] == "btnReserve"
    assert any("/frame" in frame for frame in saved["frames"])
