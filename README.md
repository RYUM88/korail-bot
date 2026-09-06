# claude

## korail-holiday-ticket

코레일 명절 승차권 예매 페이지(`bt.korail.com/holiday/main`)의 예매 버튼을
**코레일 서버 시각 기준**으로 정확한 순간에 클릭해 주는 도구.

```bash
cd korail-holiday-ticket
pip install -r requirements.txt && playwright install chromium

python -m korail_bot clock                      # 서버 시각과 내 PC 시계 차이 확인
python -m korail_bot probe                      # 예매 버튼 셀렉터 확인
python -m korail_bot run --at "+30s" --dry-run  # 리허설
python -m korail_bot run --at "2026-09-15 14:00:00" --selector "#btnReserve"
```

자세한 사용법은 [korail-holiday-ticket/README.md](korail-holiday-ticket/README.md).
