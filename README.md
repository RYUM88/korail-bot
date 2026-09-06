# claude

## korail-holiday-ticket

코레일 명절 승차권 예매 페이지(`bt.korail.com/holiday/main`)의 예매 버튼을
**코레일 서버 시각 기준**으로 정확한 순간에 클릭해 주는 도구.

### Claude 에게 시키기

내 PC 에서 Claude Code 를 켜고 말로 시키면 됩니다.
(`.claude/skills/korail-ticket/` 스킬이 처리합니다)

```
추석 예매 걸어줘. 9월 15일 오후 2시야
코레일 서버 시간 확인해줘
예매 리허설 한번 돌려봐
```

> 클라우드(Claude Code on the web) 세션에서는 `bt.korail.com` 이 조직 이그레스
> 정책으로 막혀 있어 실행할 수 없습니다. 내 PC 에서 돌려야 합니다.

### 직접 실행하기

```bash
cd korail-holiday-ticket

./yemae.sh clock                      # 서버 시각과 내 PC 시계 차이 확인
./yemae.sh login                      # 미리 로그인해 두기
./yemae.sh probe                      # 예매 버튼 셀렉터 확인
./yemae.sh "+30s" --dry-run           # 리허설
./yemae.sh "2026-09-15 14:00:00"      # 실전
```

윈도우는 `yemae.bat` (더블클릭 가능). 처음 실행하면 가상환경과 브라우저를 알아서 준비합니다.

자세한 사용법은 [korail-holiday-ticket/README.md](korail-holiday-ticket/README.md).
