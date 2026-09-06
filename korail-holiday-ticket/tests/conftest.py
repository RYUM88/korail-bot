"""테스트 공통 설정.

이 저장소의 CI 이미지에는 크로미움이 이미 깔려 있고 Playwright 가 기대하는 빌드
번호와 다를 수 있어서, 실행 파일 경로를 직접 잡아준다. 일반 PC 에서는
``playwright install chromium`` 으로 받은 기본 브라우저가 그대로 쓰인다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_PREINSTALLED = Path("/opt/pw-browsers/chromium")


def browser_executable() -> str | None:
    """테스트에 쓸 브라우저 실행 파일 경로(없으면 ``None``)."""
    from_env = os.environ.get("KORAIL_BOT_BROWSER_PATH")
    if from_env:
        return from_env
    if _PREINSTALLED.exists():
        return str(_PREINSTALLED)
    return None


@pytest.fixture(scope="session", autouse=True)
def _pin_browser_executable():
    """CLI 가 기본값으로 집어가도록 환경변수에도 심어둔다."""
    executable = browser_executable()
    if executable and not os.environ.get("KORAIL_BOT_BROWSER_PATH"):
        os.environ["KORAIL_BOT_BROWSER_PATH"] = executable
    yield
