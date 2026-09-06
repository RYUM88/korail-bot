#!/usr/bin/env bash
# 코레일 명절 승차권 예매 - 한 줄 실행기 (macOS / Linux)
#
#   ./yemae.sh                          설정 파일(my-config.json)대로 실행
#   ./yemae.sh "2026-09-15 14:00:00"    이 시각에 예매 버튼 클릭
#   ./yemae.sh login                    미리 로그인해서 저장
#   ./yemae.sh probe                    예매 버튼 셀렉터 확인
#   ./yemae.sh clock                    서버 시각 차이 확인
#
# 처음 실행하면 가상환경과 브라우저를 알아서 준비합니다.

set -eu

cd "$(dirname "$0")"

VENV=".venv"
MARKER="$VENV/.korail-ready"

python_for_venv() {
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
          >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

venv_python() {
  # 리눅스/맥은 bin, 윈도우 git-bash 는 Scripts
  if [ -x "$VENV/bin/python" ]; then
    echo "$VENV/bin/python"
  else
    echo "$VENV/Scripts/python.exe"
  fi
}

if [ ! -f "$MARKER" ]; then
  echo "처음 실행이라 준비를 합니다. 몇 분 걸립니다."
  echo
  if ! PYTHON="$(python_for_venv)"; then
    echo "오류: 파이썬 3.9 이상을 찾지 못했습니다." >&2
    echo "      https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요." >&2
    exit 1
  fi
  echo "  파이썬: $PYTHON"
  [ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
  PY="$(venv_python)"
  echo "  라이브러리 설치 중..."
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
  echo "  브라우저 내려받는 중..."
  "$PY" -m playwright install chromium
  : > "$MARKER"
  echo "준비 완료."
  echo
fi

PY="$(venv_python)"

CONFIG_ARGS=""
if [ -f my-config.json ]; then
  CONFIG_ARGS="--config my-config.json"
fi

case "${1:-}" in
  clock|login|probe|run)
    SUB="$1"
    shift
    exec "$PY" -m korail_bot $CONFIG_ARGS "$SUB" "$@"
    ;;
  "")
    exec "$PY" -m korail_bot $CONFIG_ARGS run
    ;;
  -*)
    exec "$PY" -m korail_bot $CONFIG_ARGS run "$@"
    ;;
  *)
    AT="$1"
    shift
    exec "$PY" -m korail_bot $CONFIG_ARGS run --at "$AT" "$@"
    ;;
esac
