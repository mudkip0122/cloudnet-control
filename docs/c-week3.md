# C 3주차 — 대시보드와 그래프

작업 브랜치: `c/week3-dashboard`. B 시험 모드 통합 브랜치는 대기 상태로 유지하며 이번 변경은 서버·Agent·메시지 규격을 수정하지 않습니다.

## 구현 결과

| 요구사항 | 구현 |
|---|---|
| 선택 PC 상세 | CPU·RAM %, 업로드·다운로드 Mbps, Agent 측정 UTC, 서버 최근 수신 UTC, GUI 목록 수신 UTC, 각 경과 시간 |
| Matplotlib 그래프 | CPU·RAM 시계열 및 업로드·다운로드 시계열, 시간축 UTC, CPU·RAM 축 0~100%, 트래픽 축 자동 조정 |
| PC별 버퍼 | PC ID별 최근 180개 고유 측정값. 기본 2초 수집이면 약 6분. 동일/역순 시각은 추가하지 않고 목록에서 사라진 PC의 버퍼 제거 |
| 갱신 제한 | 같은 PC의 일반 데이터 변경은 최대 1초 1회. 대기 콜백은 한 개만 유지. 선택 PC·상태 변경은 즉시 반영. Figure·Axes·Line 객체 재사용 |
| 결측·단절 | null·음수·무한대·NaN·범위 밖 사용률은 결측. 0과 구분. 결측·6초 초과 간격·확인된 단절 뒤에는 선을 연결하지 않음 |
| 오래된 값 | 측정 후 6초 초과·시각 오류·오프라인·서버 단절 때 현재 값은 —, 차트는 이전 측정 기록으로 표시 |
| 제어 제한 | 지연·단절·오프라인에서는 프로세스 버튼과 PC 제어 미리보기 버튼 비활성. 프로세스 창도 사용 불가 상태 반영 |

서버 최근 수신 시각에는 Heartbeat도 반영될 수 있으므로 측정 시각과 구분합니다. GUI 목록 수신 시각은 Tk 메인 스레드가 전체 목록을 적용한 시각입니다. 단절 중에는 마지막 시각을 유지하며 경과 시간은 증가합니다. 샘플 모드는 고정 예시임을 표시합니다.

차트의 180개 제한은 최근 측정 개수이며 서버 영속 이력 조회 기능이 아닙니다. 서버 재시작으로 목록에서 PC가 사라지면 해당 로컬 기록도 제거합니다. 이벤트 기반 이력·경고는 4주차 범위입니다.

## 실행

저장소 루트에서:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m admin_gui.sample
```

실제 서버 실행 방법은 [C 로컬 가이드](c-local-guide.md)와 [1·2주차 인계](c-week1-week2.md)를 따릅니다. 기존 인증서·토큰을 사용합니다. PC 종료·재부팅은 기존처럼 확인창 미리보기이며 실제 제어를 전송하지 않습니다.

## 검증과 남은 확인

```powershell
.\.venv\Scripts\python.exe -m unittest discover -q
.\.venv\Scripts\python.exe -m tests.smoke_dashboard
.\.venv\Scripts\python.exe -m tests.smoke_dashboard --sample
.\.venv\Scripts\python.exe -m tests.smoke_live
```

- `tests.test_charts`: PC별 버퍼 상한, 중복·역순 시각, 결측·비정상 값·단절 간격, PC 3개에 2초 간격 30분 분량의 합성 데이터 901회 입력 검증.
- `tests.test_week3_dashboard`: 실제 Tk/Matplotlib 위젯에서 같은 PC 이름을 가진 서로 다른 ID 3개 전환, CPU·RAM과 그래프 대응, 단절 시 제어 비활성·이전 기록 표시, 갱신 제한·객체 재사용 검증.
- 30분 분량 입력은 빠르게 수행하는 합성 데이터 검증입니다. 실제 30분 실행이나 운영 프로세스 메모리 측정을 완료했다는 뜻이 아닙니다.
- 실제 PC 3대·배포 서버 동시 연결과 장시간 실행은 A·B가 준비되면 공동 확인합니다.

검증 결과: 전체 테스트 101개 통과. `smoke_dashboard`, `smoke_dashboard --sample`, `smoke_live` 모두 통과. 로컬 실제 Agent의 측정값 표시와 오프라인 전환을 확인했습니다. A/B 통합 브랜치 병합 및 운영 배포는 대기 상태입니다.
