# B 시험 모드 구현 및 A·C 인계

2026-11-11. B Agent 구현 문서이며 **A 서버·공통 프로토콜·C GUI 소스는 변경하지 않았습니다.** 아래 메시지는 B가 구현한 연동 제안입니다. A·C 반영 전에는 GUI 경고까지 완료된 기능이 아닙니다.

## B가 구현한 범위

- 기본 비활성. 로컬 `--exam-mode` 시작과 별도로 `--allow-remote-exam-control`을 지정하면 GUI에서 전달할 켜기·끄기·상태 조회 명령을 처리합니다. 실제 GUI 버튼과 서버 중계는 A·C 인계 대상입니다.
- 금지 실행 파일 이름을 대소문자 무시·전체 이름 일치로 비교합니다. 경로·와일드카드는 받지 않습니다. 목록은 1~100개, 이름은 최대 128자입니다.
- 기본 1초 간격의 별도 스레드에서 검사합니다. 측정값·Heartbeat와 같은 송신 잠금을 사용합니다.
- 시험 시작 때 이미 실행 중인 프로그램도 감지합니다. 같은 PID·생성 시각은 관찰 중 한 번 알립니다. 종료를 관찰한 뒤 재등장하거나 PID의 생성 시각이 달라지면 다시 알립니다.
- 접근 불가·전체 검사 실패는 MONITOR_STATUS로 알려 감시 상태를 구분합니다. 상태가 바뀔 때만 보냅니다.
- TLS·토큰·live 모드가 필수입니다. 등록 성공 응답에 `exam_events_v1: true`가 없으면 명확한 로그와 종료 코드 2로 중단합니다. 기존 서버에 미지원 이벤트를 보내지 않습니다. 시험 모드를 사용하지 않으면 기존 등록·전송 흐름을 유지합니다.
- 연결 종료 시 감지 스레드를 정리합니다. 원격으로 켠 시험 모드는 새 연결에서 꺼짐으로 초기화하므로 상태 조회 후 다시 켜야 합니다. 로컬 `--exam-mode`를 함께 지정한 경우에는 로컬 시작 설정이 재적용됩니다. 재검사 시 같은 실행 인스턴스도 다시 보고될 수 있습니다. 오프라인 이벤트 큐·수신 ACK·재전송 보장은 없습니다.
- 프로세스 종료·실행 차단을 하지 않습니다. 사용자명·실행 경로·명령줄·창 제목·브라우저 URL·화면·키 입력은 수집하지 않습니다.

## 실행 방법

**A가 이벤트 수신 지원을 배포한 뒤**, 기존 PC-02 TLS/token 설정에 다음 옵션을 추가합니다. 금지 목록은 감독자가 정한 실제 실행 파일 이름으로 교체합니다. 인증서·토큰은 기존 파일을 그대로 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --server-name 32.236.13.91 --pc-id PC-02 --live --exam-mode --exam-id midterm-2026 --forbid-process Discord.exe --forbid-process Telegram.exe
```

선택: `--exam-poll-interval 1` (0.5~60초). 시험 ID는 영문·숫자·`_ . -` 1~64자입니다. 프로세스 제어 옵션은 필요 없습니다. 이번 작업에서는 운영 Agent 재시작이나 실제 시험 모드 활성화를 수행하지 않았습니다.

GUI 원격 켜기·끄기를 허용하고 **처음에는 꺼짐으로 대기**하려면 기존 접속 설정에 `--allow-remote-exam-control`만 추가합니다. 이 경우 시험 ID·금지 목록은 GUI 명령에서 받습니다. `--enable-process-control`은 필요 없으며 이 옵션이 프로세스 종료 권한을 열지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --server-name 32.236.13.91 --pc-id PC-02 --live --allow-remote-exam-control
```

원격 제어를 허용한 Agent는 등록 응답에 `exam_events_v1: true`와 `exam_control_v1: true`가 모두 있어야 시작합니다. A가 지원하기 전에는 실행을 중단합니다.

## A에게 요청할 작업

1. 인증된 Agent의 EXAM_EVENT를 검증·저장·관리자에게 중계합니다. 이벤트의 pc_id는 인증 세션 ID와 같아야 하며 Admin이 Agent 이벤트를 위조해 보내지 못하도록 역할을 검증합니다.
2. 아래 v1 규격을 지원한 서버만 REGISTER_RESULT에 `exam_events_v1: true`를 추가합니다. 기존 필드는 그대로 둡니다. 새 ACK를 보내지 않습니다. 현재 B 수신기는 EXAM_EVENT_ACK를 처리하지 않습니다.
3. 타입·버전·kind·필드 길이·시간·유한한 생성 시각·양의 PID 등을 검증하고 크기·빈도 제한을 둡니다. 금지 이름은 최대 128자, 시험 ID는 위 제한을 따릅니다. 서버 수신 시각도 별도 기록합니다.
4. event_id를 저장해 동일 이벤트 중복 저장을 막습니다. 재접속으로 새 event_id가 생긴 동일 프로세스는 필요하면 시험 ID·PC ID·PID·생성 시각으로 묶어 표시합니다. 재접속은 새 관찰이므로 원본 이력은 보존합니다.
5. 연결 해제·Agent 미실행·Heartbeat 손실 상태를 감시 공백으로 전달합니다. 이벤트가 없다는 이유로 '부정행위 없음'이나 '시험 모드 켜짐'으로 판단하지 않습니다.
6. 아래 SET_EXAM_MODE·GET_EXAM_STATUS를 관리자 명령 허용 목록에 추가하고 기존 command_id·target_id·expires_at 검증과 요청자에게 결과를 돌려주는 중계를 적용합니다. 정책 내용도 검증합니다. 서버의 시험별 정책 대조·허용 PC·감독자 권한과 기록 보관 기간은 A가 결정합니다. 이 명령은 단일 COMMAND_RESULT이며 프로세스 목록처럼 분할하지 않습니다.
7. 켜기·끄기·상태 조회 중계까지 지원할 때만 `exam_control_v1: true`를 등록 응답에 추가합니다. 상태 변경은 요청한 관리자 외의 감독 화면에도 전달하고, 새 Agent 연결에서는 기존 켜짐 상태를 미확인으로 무효화합니다. B는 별도의 '꺼짐' EXAM_EVENT를 보내지 않으므로 명령 결과의 exam_mode를 상태 갱신 근거로 사용합니다.

등록 응답 추가 필드 예시:

```json
{"type":"REGISTER_RESULT","ok":true,"pc_id":"PC-02","heartbeat_interval":5,"exam_events_v1":true,"exam_control_v1":true}
```

감지 이벤트 예시 (`event_id`는 매 이벤트 UUID, 생성 시각은 Unix 초):

```json
{"type":"EXAM_EVENT","version":1,"event_id":"d30a538a-f2f7-4379-891f-f5377fc27ef2","pc_id":"PC-02","exam_id":"midterm-2026","kind":"FORBIDDEN_PROCESS_DETECTED","timestamp":"2026-11-11T03:00:00Z","process":{"pid":1234,"name":"Discord.exe","create_time":1794365900.25}}
```

감시 상태는 시작 직후 및 상태 전환 때 전송합니다. `ok`는 해당 검사에서 이름 조회 오류가 없었다는 의미이며 시험 준수 보증이 아닙니다. `partial`은 일부 프로세스 정보 접근 불가, `unavailable`은 전체 열거 실패입니다. `unreadable_count`는 이번 검사에서 정보를 읽지 못한 PID 수이며 전체 열거 실패 때는 파악한 일부 수치일 수 있습니다.

```json
{"type":"EXAM_EVENT","version":1,"event_id":"fab92923-e098-4b75-a071-345f23d80baa","pc_id":"PC-02","exam_id":"midterm-2026","kind":"MONITOR_STATUS","timestamp":"2026-11-11T03:00:00Z","status":"partial","unreadable_count":2,"poll_interval":1}
```

## C에게 요청할 작업

- A가 중계한 이벤트를 GUI 메인 스레드에서 처리하고 PC·시험별로 표시합니다. Agent와 직접 연결하지 않습니다.
- 금지 프로그램 감지 시 PC ID, 시험 ID, 이름, PID, 감지 시각을 경고와 이력에 표시합니다. '금지 프로그램 실행 감지'라고 표시하며 부정행위 확정으로 표현하지 않습니다.
- MONITOR_STATUS의 정상·일부 확인 불가·감시 불가를 구분하고 서버 연결 해제도 표시합니다. 감지 경고와 감시 상태는 별개로 관리합니다. 재접속 뒤 상태 이벤트를 받을 때까지 감시 중이라고 표시하지 않습니다.
- 같은 event_id 중복 경고를 막고, 브라우저처럼 프로세스가 여럿인 앱은 묶어서 표시할 수 있습니다. 종료·경고 해제 이벤트는 없으므로 '현재 실행 중' 대신 '감지 이력'으로 표시합니다.
- 온라인 PC를 선택하고 시험 ID·금지 실행 파일 목록·검사 간격을 입력하는 화면과 '시험 모드 켜기', '끄기', '상태 새로고침' 버튼을 추가합니다. 아래 명령으로 요청하며 Agent 재실행 없이 전환됩니다. 버튼 구현 자체는 C 담당입니다.
- 전송 중 버튼 중복 클릭을 막고, 성공 COMMAND_RESULT를 받기 전에는 켜짐·꺼짐으로 확정하지 않습니다. 켜짐 응답 직후에는 '검사 시작 중'이고 첫 MONITOR_STATUS를 받은 뒤 감시 상태를 표시합니다. 끄기에는 현재 시험 ID를 넣습니다.
- 타임아웃·연결 오류는 결과 미확인으로 표시하고 자동 재전송하지 않습니다. 재연결 후 GET_EXAM_STATUS로 확인합니다. 원격 모드는 재접속 시 꺼지므로 자동으로 켜지 말고 감독자가 다시 켜도록 합니다. 기존 감지 이력은 보존합니다.

## GUI 제어 명령 계약 (B 구현 완료, A·C 반영 필요)

공통 필드: `type: COMMAND`, 새 `command_id`(1~128자), `target_id`, 시간대가 포함된 `expires_at`(미래 60초 이내). 실제 요청 때 현재 시각 기준으로 생성하며 아래 고정 예시 시각을 그대로 사용하지 않습니다. 중복 명령 ID는 최대 60초·4096개 메모리 캐시로 거절합니다. 같은 Agent 프로세스의 재접속 동안 캐시는 유지하지만 Agent 재시작 후 영속 중복 방지는 A 책임입니다.

켜기 / 정책 교체:

```json
{"type":"COMMAND","command_id":"exam-on-unique-1","target_id":"PC-02","action":"SET_EXAM_MODE","enabled":true,"exam_id":"midterm-2026","forbidden_processes":["Discord.exe","Telegram.exe"],"poll_interval":1,"expires_at":"2026-11-11T03:00:30Z"}
```

`enabled`는 반드시 JSON boolean입니다. 켜기에는 유효한 시험 ID·금지 목록(배열 1~100개)이 필수이며 검사 간격은 생략 시 1초입니다. 잘못된 새 정책은 현재 실행 중인 정책을 바꾸지 않습니다. 성공한 켜기는 같은 시험 ID여도 감지 이력을 초기화하고 다시 검사합니다.

끄기:

```json
{"type":"COMMAND","command_id":"exam-off-unique-2","target_id":"PC-02","action":"SET_EXAM_MODE","enabled":false,"exam_id":"midterm-2026","expires_at":"2026-11-11T03:00:30Z"}
```

시험 ID가 현재/직전 시험과 다르면 EXAM_ID_MISMATCH입니다. 같은 시험을 이미 껐다면 새 명령 ID의 끄기는 성공합니다. 연결 이후 한 번도 시험을 켜지 않았다면 상태를 조회해 꺼짐을 확인합니다. 끄기 응답 이후에는 그 연결의 이전 시험 감지 이벤트가 뒤늦게 전송되지 않습니다. 전환 시 수집·Heartbeat는 계속됩니다.

상태 조회:

```json
{"type":"COMMAND","command_id":"exam-status-unique-3","target_id":"PC-02","action":"GET_EXAM_STATUS","expires_at":"2026-11-11T03:00:30Z"}
```

성공 응답 예시:

```json
{"type":"COMMAND_RESULT","pc_id":"PC-02","command_id":"exam-on-unique-1","ok":true,"code":"EXAM_MODE_ENABLED","exam_mode":{"enabled":true,"exam_id":"midterm-2026","forbidden_processes":["discord.exe","telegram.exe"],"poll_interval":1,"monitor_status":"starting"}}
```

code는 켜기 EXAM_MODE_ENABLED, 끄기 EXAM_MODE_DISABLED, 조회 EXAM_STATUS입니다. exam_mode.monitor_status는 `starting`, `ok`, `partial`, `unavailable`, `off`입니다. 꺼짐은 enabled=false, forbidden_processes=[], poll_interval=null이고 exam_id는 직전 시험 ID 또는 null입니다. 켜기 성공 결과가 해당 연결의 첫 감지 이벤트보다 먼저 전송됩니다.

실패는 ok=false와 code만 반환하며 exam_mode는 없습니다. EXAM_CONTROL_DISABLED(로컬 원격 허용 옵션 없음), INVALID_COMMAND_ID, WRONG_TARGET, INVALID_EXPIRY, EXPIRED_OR_TOO_FAR, DUPLICATE_COMMAND, COMMAND_CAPACITY, INVALID_EXAM_SETTINGS, EXAM_ID_MISMATCH, UNSUPPORTED_ACTION을 처리합니다. A 서버의 TARGET_OFFLINE·UNSUPPORTED_ACTION·TLS_REQUIRED도 GUI에 구분해서 표시합니다.

## 검증 및 한계

`python -m unittest tests.test_exam_commands tests.test_exam tests.test_heartbeat tests.test_agent tests.test_agent_security tests.test_processes -v`로 관련 48개 테스트를 통과했습니다. 원격 켜기→감지→끄기→상태 조회 실제 소켓 통신, 잘못된 정책 보존, 중복·만료·대상 검증, 재접속 초기화, TLS 요구, 프로세스 제어와 권한 분리, 기존 실행 감지·PID 재사용·정보 접근 실패·스레드 정리를 확인했습니다. 운영 인증정보는 사용하지 않습니다. A 서버 및 C GUI를 통한 전체 통합 검증은 아직 수행하지 않았습니다.

이 방식은 이름 기반 주기 검사입니다. 검사 사이에 시작·종료한 프로그램, 실행 파일 이름 변경, 접근 제한 프로세스는 놓칠 수 있습니다. 브라우저 탭·사이트·확장 프로그램·다른 기기는 판별하지 않습니다. 실제 수험자에게 시험 모드와 수집 범위를 안내하고 승인된 시험 PC에서 사용합니다. A 수신·C GUI 표시와 실제 PC 시험은 인계 후 공동 검증이 필요합니다.
