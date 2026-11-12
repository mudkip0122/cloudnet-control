# A 전경 창 감시 이벤트 연동 및 C 인계

작성일: 2026-11-12. 브랜치: `integration/a-exam-batch-control`.
B 전경 감시 기준: `b6f76c0`. C 현재 기준: `b257588`.
A의 이벤트 검증·저장·중계·지원 플래그 구현: `18139f1`.
B 규격 원문: [b-foreground-handoff.md](b-foreground-handoff.md).

## A 구현

B 브랜치의 새 커밋을 통합 브랜치에 병합했습니다. A가 직접 편집한 범위는 서버·A 테스트·A 문서입니다. B Agent와 C GUI 소스는 직접 편집하지 않았고 기존 인증정보를 유지했습니다.

기존 EXAM_EVENT version=1에 아래 종류를 추가했습니다.

| kind | 추가 필드 | 검증 |
|---|---|---|
| EXAM_WINDOW_CHANGED | transition, previous_process, process | transition은 left/returned, 두 프로세스 모두 양의 정수 PID·유한한 양의 create_time·경로 없는 1~128자 이름 |
| FOREGROUND_MONITOR_STATUS | status | ok/unavailable만 허용 |

left는 Chrome→다른 프로세스, returned는 다른 프로세스→Chrome인지 이름을 casefold해 대조합니다. 두 프로세스 이름·전환 방향이 모순되면 거절합니다. pc_id와 인증 세션 일치, version=1, 시험 ID·이벤트 ID·시간대 있는 timestamp 검증은 기존과 같습니다. 틀린 이벤트는 저장/중계하지 않습니다.

기존 SQLite exam_events에 서버 수신 시각과 검증된 이벤트를 저장합니다. event_id 중복은 저장·중계하지 않으며 PC별 초당 200개 제한과 관리자별 32개 큐 정책도 유지합니다. subscribe_exam:true인 TLS 관리자에게 기존 이벤트와 같은 원문 최상위 구조로 전달합니다. 새 ACK나 래퍼를 추가하지 않습니다.

전환 예시:

```json
{"type":"EXAM_EVENT","version":1,"pc_id":"PC-02","exam_id":"exam-1","event_id":"example-unique-event","timestamp":"2026-11-12T08:00:00Z","kind":"EXAM_WINDOW_CHANGED","transition":"left","previous_process":{"pid":100,"name":"chrome.exe","create_time":1794470000.0},"process":{"pid":200,"name":"notepad.exe","create_time":1794470001.0}}
```

감시 공백 예시:

```json
{"type":"EXAM_EVENT","version":1,"pc_id":"PC-02","exam_id":"exam-1","event_id":"example-unique-gap","timestamp":"2026-11-12T08:00:01Z","kind":"FOREGROUND_MONITOR_STATUS","status":"unavailable"}
```

FOREGROUND_MONITOR_STATUS는 기존 MONITOR_STATUS와 독립적입니다. 활성 창 감시가 unavailable이어도 금지 프로세스 검사는 정상일 수 있습니다. A는 이 이벤트로 exam_mode.monitor_status를 덮어쓰지 않습니다. 별도의 전경 감시 상태 스냅샷 필드는 이번 규격에 추가하지 않았습니다. 중간에 구독한 GUI가 아직 이벤트를 받지 않았다면 전경 감시 상태는 미확인입니다.

## 지원 협상

TLS·인증·감사 저장소와 이벤트 처리 경로가 준비된 서버의 REGISTER_RESULT에 다음 JSON boolean을 추가합니다.

```json
{"type":"REGISTER_RESULT","ok":true,"pc_id":"PC-02","heartbeat_interval":5,"exam_events_v1":true,"exam_control_v1":true,"exam_foreground_v1":true,"rtt_v1":true}
```

B는 매 연결에서 exam_foreground_v1이 정확히 true인지 확인하고 시험 모드 안에서만 활성 창을 읽습니다. 지원 필드가 없으면 기존 금지 프로세스 감지만 유지합니다. A 로컬 개발의 비TLS 또는 감사 저장소 없는 세션에는 시험/전경 지원 플래그를 광고하지 않습니다.

## C 연동 상태와 전달 사항

초기 C `35004c3`는 두 새 kind를 거절했지만, 후속 `b257588`에는 파서·이력·경고·감시 공백 표시가 구현되어 있습니다. 최신 C를 A 브랜치에 충돌 없이 병합했습니다. C 인계 문서의 A 미지원 안내는 이전 A `c5ecfef`/`646d171` 기준이며 A `18139f1`부터 새 이벤트를 지원합니다. C 소스를 A가 대신 수정하지 않았습니다.

> A 전경 지원은 18139f1에 구현되어 있고 C b257588을 integration/a-exam-batch-control에 병합했습니다. 기존 subscribe_exam:true 연결에서 새 원문 이벤트를 받으며 exam_foreground_v1:true를 제공합니다. 실제 C 수신 코드를 A TLS 서버에 연결해 이탈·복귀·감시 공백/복구·중복 억제·모드 끄기 후 상태 초기화와 이력 보존을 확인했습니다. 이 브랜치의 최신 통합 커밋으로 실제 Windows 창 전환 공동 검증을 진행하면 됩니다. 운영 EC2는 아직 배포 전입니다.

C 표시 기준:

- left는 Chrome 이탈, returned는 Chrome 복귀. PC·시험·시각·이전/현재 프로세스 이름을 표시합니다.
- unavailable은 감시 공백이며 정상 응시나 부정행위의 근거가 아닙니다. 이벤트가 없는 경우도 정상으로 단정하지 않습니다.
- 첫 관찰·공백 복구는 B의 기준점입니다. A/C가 누락된 이탈을 만들어내지 않습니다.
- 공백·모드 초기화·새 Agent 연결을 가로질러 이탈 지속 시간을 계산하지 않습니다. 현재 B 이벤트에 지속 시간 필드는 없습니다.
- 이력은 현재 실행 상태와 구분합니다. 창 제목·URL·탭·화면·키 입력은 수집하지 않습니다. Chrome 내 창/탭 이동은 구분하지 않습니다.
- 현재 모드는 기존 exam_mode/agent_session_id 규격으로 무효화·재조회합니다. 새 전경 이벤트로 꺼진 모드를 켜지 않습니다.

## B에게 전달할 내용

> b6f76c0와 A 18139f1을 통합했고 C b257588의 새 이벤트 처리도 병합했습니다. B 메시지 변경 요청은 없습니다. 운영 Agent 재실행은 공동 배포 안내 후 진행해 주세요. 기존 PC ID·토큰·인증서는 유지합니다.

## 검증과 남은 범위

tests.test_server_foreground는 B의 실제 생성 이벤트 형식, 잘못된 전환·프로세스 식별값·status·PC 위장 거절, TLS 지원 플래그, 저장·원문 중계·중복 억제, 기존 프로세스 감시 상태 보존을 검증합니다. B stream과 A TLS 서버를 연결하고 Windows 활성 창 조회만 모의해 left/returned/ok/unavailable의 전체 경로를 확인합니다. 실제 사용자 창을 전환하거나 앱을 종료하지 않습니다.

2026-11-12 최초 A 검증: 관련 27개와 당시 C를 포함한 전체 199개 통과, skip 없음. 후속 C b257588 병합 후에는 A의 실제 C 클라이언트 TLS 검증도 추가했습니다. 해당 테스트는 소켓 연결 대상만 실제 A TLS 세션으로 연결하고 C의 등록·수신·검증·큐·모델·이력 문구 처리는 수정 없이 실행합니다. left/returned/ok/unavailable 전달, DB 5건과 C 이력 5건 일치, 중복 억제, 모드 끄기 후 감시 상태 초기화·이력 보존을 확인합니다.

최종 통합 결과: B b6f76c0·C b257588·A 18139f1과 추가 공동 검증을 포함한 **전체 211개 통과, skip 없음**. `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`를 C Tk 화면 포함해 샌드박스 밖에서 실행했습니다. A가 직접 편집한 후속 파일은 A 테스트와 A 문서뿐입니다.

남은 작업은 운영 서버 DB 쓰기 경로 및 배포 일정 준비 → EC2 배포 → 실제 Windows PC의 Chrome 이탈/복귀·감시 공백·단절/복구 공동 검증입니다. 창 조회 API를 모의한 로컬 테스트와 실제 사용자의 데스크톱 창 전환 시연은 구분합니다. 이번 작업에서 main 병합·운영 배포는 하지 않았습니다.
