# C 담당 로컬 작업 안내

프로세스 조회·보호 종료의 TLS 연결 조건과 화면 사용법은 [프로세스 GUI 안내](gui-process-control.md)를 참고하세요.

C는 `admin_gui/`의 PC 목록·선택 상세·그래프·이력·명령 결과 화면을 담당합니다.
기본 실행은 로컬 검증 모드입니다. TLS 실행과 1·2주차 완료 현황·A/B 인수인계는 [1·2주차 구현 안내](c-week1-week2.md)를 참고하세요.

## 지금 실행 가능한 항목

저장소 루트 PowerShell에서 각 명령을 실행합니다.

```powershell
# 서버 없이 샘플 화면 검토 (실제 값 아님)
.\.venv\Scripts\python.exe -m admin_gui.sample

# 실제 측정: 서버, Agent, GUI를 각 터미널에서 실행
.\.venv\Scripts\python.exe -m server.main
.\.venv\Scripts\python.exe -m agent.main --live --pc-id $env:COMPUTERNAME
.\.venv\Scripts\python.exe -m admin_gui.main
```

이미 실행 중인 서버·Agent가 있으면 GUI만 실행합니다. 기본 주소는 `127.0.0.1:5000`이며, 원격 접속은 TLS·역할별 토큰 옵션이 필요합니다.
GUI에서 행을 선택하면 CPU·RAM·네트워크·측정 시각·서버 수신 시각·측정 후 경과를 봅니다.
상단에는 서버 주소·포트, 연결 모드와 관리자 인증 상태가 표시됩니다. TLS 설정만으로 인증 성공을 표시하지 않고 실제 등록·목록 수신 후 성공을 표시합니다.
`PC 종료`·`PC 재부팅`은 현재 확인창만 표시하며 실제 PC 제어는 수행하지 않습니다. 프로세스 종료와 구분됩니다.
샘플 모드는 실제 화면과 같은 구성에서 정상·데이터 없음·오프라인을 보여주며 네트워크에 접속하지 않습니다.
목록 갱신 중에도 PC ID로 선택을 유지합니다. 측정 후 6초를 넘으면 지연으로 표시합니다.
오프라인·서버 단절·시각 오류일 때 현재 사용률은 `—`로 표시하며, 0은 `0.0`으로 표시합니다.
서버가 끊겨도 마지막 PC 목록과 선택을 유지하고 재연결합니다.

## 로컬 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_dashboard
.\.venv\Scripts\python.exe -m tests.smoke_live
```

- `smoke_dashboard`: 샘플 PC 3대 전환, 선택 유지, 오프라인, 지연, 서버 단절, 빈 목록을 위젯에서 검증합니다. 실제 PC 3대 검증을 대신하지 않습니다.
- `smoke_live`: 별도 포트 15002에서 실제 CPU·RAM·네트워크 수집 → 서버 → GUI 표시와 Agent 종료 후 오프라인 전환을 검증합니다.
- 직접 확인: 행 클릭, 창 크기 변경, GUI 종료·재실행, Agent 중지·재시작을 확인합니다.

## A·B에게 공유할 화면 필드

| 제공 담당 | 필드 | 화면 용도 |
|---|---|---|
| A | `pc_id`, `pc_name`, `online` | 목록, 고유 대상 선택, 연결 상태 |
| A | `last_received` | 서버 최근 수신 시각 UTC |
| B → A | `metrics.timestamp` | 측정 시각 UTC, 지연 표시 |
| B → A | `cpu_percent`, `ram_percent` | 0~100 %, 소수 1자리 |
| B → A | `upload_mbps`, `download_mbps` | Agent 기준 Mbps, 소수 2자리 |

수집 2초·목록 조회 1초를 사용합니다. null은 결측값이며 0과 구분합니다.
기존 `REGISTER` / `LIST_DEVICES` / `DEVICE_LIST` 메시지를 사용하고 서버 규격을 변경하지 않습니다.

## 다음 C 작업

1. PC별 길이가 제한된 버퍼와 CPU·RAM·네트워크 그래프 추가.
2. A와 이력 조회 요청·응답, 경고 이벤트 규격 및 ERD 초안 협의.
3. A·B의 명령 처리 구현에 맞춰 대상 확인 창과 명령 상태 표시 연결.
4. CustomTkinter 화면 전환과 실제 PC 3대 장시간 사용성 검증.

TLS·관리자 인증·저장 이력·서버 경고·실제 종료/재부팅은 현재 구현에 없으므로
로컬 화면 검증 완료와 별도로 추적합니다. DB 파일은 GUI에서 직접 열지 않습니다.
