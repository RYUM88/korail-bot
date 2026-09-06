@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 코레일 명절 승차권 예매 - 한 줄 실행기 (Windows)
rem
rem   yemae.bat                          설정 파일(my-config.json)대로 실행
rem   yemae.bat "2026-09-15 14:00:00"    이 시각에 예매 버튼 클릭
rem   yemae.bat login                    미리 로그인해서 저장
rem   yemae.bat probe                    예매 버튼 셀렉터 확인
rem   yemae.bat clock                    서버 시각 차이 확인

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%VENV%\.korail-ready" (
  echo 처음 실행이라 준비를 합니다. 몇 분 걸립니다.
  echo.
  python -m venv "%VENV%"
  if errorlevel 1 (
    echo 오류: 파이썬 3.9 이상이 필요합니다.
    echo       https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요.
    pause
    exit /b 1
  )
  echo   라이브러리 설치 중...
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet -r requirements.txt
  echo   브라우저 내려받는 중...
  "%PY%" -m playwright install chromium
  type nul > "%VENV%\.korail-ready"
  echo 준비 완료.
  echo.
)

set "CONFIG="
if exist my-config.json set "CONFIG=--config my-config.json"

if "%~1"=="" (
  "%PY%" -m korail_bot %CONFIG% run
) else if "%~1"=="clock" (
  "%PY%" -m korail_bot %CONFIG% clock
) else if "%~1"=="login" (
  "%PY%" -m korail_bot %CONFIG% login
) else if "%~1"=="probe" (
  "%PY%" -m korail_bot %CONFIG% probe
) else if "%~1"=="run" (
  "%PY%" -m korail_bot %CONFIG% run %2 %3 %4 %5 %6 %7 %8 %9
) else (
  "%PY%" -m korail_bot %CONFIG% run --at "%~1" %2 %3 %4 %5 %6 %7 %8 %9
)

pause
