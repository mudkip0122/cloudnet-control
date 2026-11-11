# A·C 통합 작업 안내 — B Agent 구현 기준

작성일: 2026-11-11

이 문서는 B가 구현한 Agent 기능을 A 서버와 C GUI에 연결하기 위한 작업 목록과 메시지 규격입니다. A·C의 실제 작업 완료 여부를 확인한 문서가 아니며, 체크박스는 담당자가 구현·검증한 뒤 표시합니다. 이번 B 작업에서는 A 서버·공통 코드·C GUI 소스를 변경하지 않았습니다.

## 1. 현재 구현과 역할

| 기능 | B Agent 상태 | A 담당 | C 담당 |
|---|---|---|---|
| CPU·RAM·네트워크 | 2초 주기 수집·METRICS 전송 구현 | PC별 수신·보관·조회·중계 확인 | PC 3대를 한 화면에서 비교 표시 |
| 연결 관리 | TLS·토큰 인증, 고유 ID, Heartbeat, 재접속 구현 | 인증·중복 ID·타임아웃·온라인 상태 관리 | 연결·미수신·오프라인 표시 |
| 프로세스 조회·개별 종료 | 목록 분할 응답·보호 정책·PID 식별 검증 구현 | 관리자 권한 검증·요청/결과 중계 확인 | 기존 목록·개별 종료 화면 연동 확인 |
| 같은 이름 그룹·일괄 종료 | 명시적 PID 목록의 보호 일괄 종료 구현 | STOP_PROCESSES 허용·중계·감사 기록 | 그룹·펼치기·확인창·개별 결과 표시 |
| 시험 모드 | 원격 켜기·끄기·상태 조회, 금지 프로그램 감지 구현 | 명령 중계·이벤트 검증/저장/중계 | 모드 버튼·감시 상태·경고·감지 이력 |
| RTT | 요청/응답 시간 측정·METRICS 추가 필드 구현 | RTT_PONG 응답·필드 보관/중계 | PC별 서버 RTT(ms) 표시 |

B의 구현 완료는 A·C 전체 기능의 완료를 의미하지 않습니다. 실제 PC 3대·서버·GUI 공동 검증이 남아 있습니다. PC 종료·재부팅 실행과 브라우저 탭/URL 감시는 현재 구현 범위가 아닙니다.

### 코드 공유 상태

- 시험 모드·일괄 종료·PC 실행 파일은 GitHub `b/exam-mode-batch-control` 브랜치의 커밋 `e396758`에 푸시되어 있습니다.
- RTT 구현·테스트와 이 종합 문서는 후속 커밋에 함께 포함합니다. 공유 위치는 동일한 `b/exam-mode-batch-control` 브랜치이며, 통합 시 최신 커밋을 사용합니다.
- A·C는 사용할 B 버전과 규격을 먼저 맞춥니다. RTT 코드는 공유된 커밋을 확인한 뒤 통합합니다.

## 2. 공통 통신 규칙

- 기존 TCP/TLS 연결과 `common.protocol`의 UTF-8 JSON + 4바이트 big-endian 길이 헤더를 사용합니다. 최대 메시지는 65,536바이트입니다.
- GUI는 Agent에 직접 연결하지 않습니다. 관리자 인증을 거친 A 서버로 요청하고, A가 해당 Agent에 중계합니다.
- 명령 공통 필드는 `type: COMMAND`, `command_id`, `target_id`, `action`, `expires_at`입니다.
- `command_id`는 새 고유 문자열 1~128자, `expires_at`은 시간대가 포함된 ISO 8601이며 Agent 검증 시 미래 60초 이내여야 합니다. C는 전송 시 현재 시각 기준으로 생성합니다.
- Agent 명령 중복 방지는 메모리 캐시 60초·최대 4096개입니다. 같은 프로세스의 재접속에는 유지하지만 Agent 재시작에는 초기화됩니다. A는 영속 감사 기록과 재전송 방지 범위를 관리합니다.
- C는 응답의 `command_id`·PC ID를 대조하고 타임아웃/연결 단절 시 결과를 미확인으로 처리합니다. 종료·모드 변경 요청을 자동 재전송하지 않습니다.
- 토큰·개인키·인증정보 파일은 로그·문서·Git에 넣지 않습니다. 기존 인증서·토큰을 사용합니다.

## 3. A가 해야 할 작업

### A-1. 등록 응답의 지원 필드

- [ ] 아래 필드는 해당 기능의 수신·응답·중계를 구현한 뒤에만 `true`로 보냅니다.

| REGISTER_RESULT 필드 | 의미 | B 동작 |
|---|---|---|
| `heartbeat_interval: 5` | Heartbeat 5초 주기 지원 | CPU/RAM 수집과 독립적으로 송신 |
| `exam_events_v1: true` | 시험 이벤트 수신 지원 | 로컬 시험 모드 또는 원격 시험 제어 허용 시 필수 |
| `exam_control_v1: true` | 시험 켜기·끄기·조회 명령 지원 | 원격 시험 제어 허용 시 필수 |
| `rtt_v1: true` | RTT_PING/PONG 및 METRICS RTT 필드 지원 | 자동 측정 시작; 필드가 없으면 RTT만 비활성 |

```json
{"type":"REGISTER_RESULT","ok":true,"pc_id":"PC-02","heartbeat_interval":5,"exam_events_v1":true,"exam_control_v1":true,"rtt_v1":true}
```

**배포 주의:** `--allow-remote-exam-control`로 시작한 Agent는 시험 지원 필드 2개가 없으면 등록 후 종료 코드 2로 중단합니다. 서버를 나중에 업데이트해도 이미 종료된 Agent는 다시 실행해야 합니다. RTT 미지원은 Agent 종료 사유가 아닙니다. 기능을 실제로 구현하지 않고 지원 필드만 추가하지 않습니다.

### A-2. CPU·RAM·네트워크와 상태

- [ ] PC ID별 최신 METRICS와 측정 시각을 보관하고 PC 3대 정보를 관리자에게 전달합니다.
- [ ] `cpu_percent`, `ram_percent`는 %, `upload_mbps`, `download_mbps`는 Mbps입니다. null을 0으로 바꾸지 않습니다.
- [ ] 같은 ID 중복 접속·잘못된 토큰·세션과 다른 pc_id를 거절합니다.
- [ ] 연결 종료와 생존 신호 타임아웃을 구분해 온라인/오프라인을 갱신합니다. 기존 계획의 5초 Heartbeat·15초 타임아웃과 배포 설정을 확인합니다.
- [ ] Agent 재등록 시 이전 연결의 최신 값·감시 상태를 현재 상태로 재사용하지 않습니다.

### A-3. 명령 허용 목록 및 결과 중계

- [ ] 기존 LIST_PROCESSES·STOP_PROCESS 처리에 아래 3개 action을 추가합니다.

| action | 요청의 추가 필드 | 성공 응답 code | 응답 형태 |
|---|---|---|---|
| `SET_EXAM_MODE` | enabled, exam_id, 켜기 시 forbidden_processes, 선택 poll_interval | EXAM_MODE_ENABLED / EXAM_MODE_DISABLED | 단일 COMMAND_RESULT |
| `GET_EXAM_STATUS` | 없음 | EXAM_STATUS | 단일 COMMAND_RESULT |
| `STOP_PROCESSES` | targets 배열 | PROCESS_BATCH_RESULT | 단일 COMMAND_RESULT |

- [ ] 관리자 권한·대상 PC·TLS·만료·명령 ID와 입력을 검증한 뒤 전달합니다.
- [ ] 응답이 해당 요청의 Agent 세션·PC·command_id에 속하는지 확인합니다.
- [ ] 새 3개 action에 LIST_PROCESSES의 페이지 조합 규칙을 적용하지 않습니다. 기존 목록은 40행씩 분할되어 오는 규칙을 유지합니다.
- [ ] 요청자에게 결과를 전달하고 모드 상태는 다른 감독 화면에도 일관되게 알립니다. 관리자 구독·상태 조회에서 전달할 형태는 C와 합의합니다.
- [ ] 실행자·명령 ID·대상 PC·설정/확정 대상·시각·개별 결과를 감사 기록에 남깁니다. 일부 실행 후 연결이 끊길 수 있음을 고려합니다.

### A-4. 시험 이벤트

- [ ] Agent 역할의 EXAM_EVENT만 허용하고 pc_id가 인증 세션과 같은지 확인합니다.
- [ ] version=1, event_id, exam_id, timestamp, kind 및 kind별 필드를 검증합니다. 시험 ID는 영문·숫자·`_ . -` 1~64자입니다.
- [ ] event_id로 중복 저장을 방지하고 서버 수신 시각을 별도로 기록합니다. 시험별 정책·감독자 열람 권한·보관 기간을 정합니다.
- [ ] FORBIDDEN_PROCESS_DETECTED와 MONITOR_STATUS를 C에 중계합니다. 형식·메시지 크기·빈도 제한을 적용합니다.
- [ ] EXAM_EVENT_ACK 같은 새 응답은 보내지 않습니다. 현재 B는 이를 지원하지 않습니다.
- [ ] 새 Agent 연결/단절 때 기존 시험 상태를 무효화합니다. 모드 끄기 상태는 COMMAND_RESULT의 exam_mode로 갱신합니다. 별도의 '꺼짐' 이벤트는 없습니다.
- [ ] 이벤트가 없다는 이유로 정상 감시나 부정행위 없음을 확정하지 않습니다. 오프라인 감지 이력의 큐·재전송 보장은 없습니다.

### A-5. RTT

- [ ] Agent의 RTT_PING에 같은 pc_id·probe_id로 RTT_PONG을 즉시 반환합니다. 관리자에게 PING을 중계하지 않습니다.
- [ ] probe_id는 비어 있지 않은 문자열 최대 128자이며, pc_id는 세션 ID와 같아야 합니다.
- [ ] 기존 송신 잠금을 사용해 프레임을 보호합니다. 양쪽 시스템 시각 차이로 RTT를 계산하지 않습니다. 계산은 B가 단조 시계로 수행합니다.
- [ ] METRICS에 추가되는 rtt_ms·rtt_status·rtt_timestamp를 검증·보관·관리자 중계합니다.
- [ ] rtt_ms는 null 또는 유한한 0 이상 숫자입니다. rtt_status가 ok일 때만 숫자이고 pending/timeout/stale이면 null입니다. rtt_timestamp는 null 또는 시간대가 있는 ISO 8601입니다.
- [ ] 연결 해제 시 오래된 RTT가 현재 값으로 남지 않도록 합니다. RTT 타임아웃만으로 PC를 오프라인 처리하지 않습니다.

## 4. C가 해야 할 작업

### C-1. PC 3대 비교 화면

- [ ] PC-01·PC-02·PC-03의 CPU(%)·RAM(%)·온라인 상태·최근 측정 시각을 한 화면에 표시합니다. 네트워크(Mbps)와 RTT(ms)도 PC별로 구분합니다.
- [ ] 표시 기준은 PC 이름이 아니라 고유 PC ID입니다. 미수신·null·오프라인은 0과 구분하고 오래된 값을 숨깁니다.
- [ ] 수신 작업과 GUI 메인 스레드를 분리하고, 이벤트/명령 결과가 최신 값 전용 큐에서 사라지지 않도록 처리합니다.

### C-2. 시험 모드 버튼과 경고

- [ ] 온라인 PC 선택 후 시험 ID·금지 실행 파일 이름 목록·검사 간격을 입력하는 화면을 만듭니다.
- [ ] '켜기', '끄기', '상태 새로고침'을 아래 명령에 연결하고 전송 중 중복 클릭을 막습니다. 여러 PC에 적용하려면 PC별 고유 명령을 보내고 결과를 각각 확인합니다. B의 명령 대상은 항상 PC 한 대입니다.
- [ ] 성공 결과를 받기 전에는 모드 상태를 확정하지 않습니다. 켜기 직후 starting은 '검사 시작 중'으로 표시합니다.
- [ ] MONITOR_STATUS를 정상(ok)·일부 확인 불가(partial)·감시 불가(unavailable)로 표시합니다. 모드 꺼짐(off), 연결 끊김, 상태 미확인도 구분합니다.
- [ ] 금지 프로그램 감지 시 PC·시험 ID·프로그램 이름·PID·감지 시각을 경고와 이력에 표시하고 event_id 중복을 억제합니다. '부정행위 확정'으로 표시하지 않습니다.
- [ ] 감지 기록과 현재 실행 여부를 구분합니다. 프로세스 종료/경고 해제 이벤트는 없으므로 자동으로 '현재 실행 중'이라 단정하지 않습니다.
- [ ] 재접속 후 GET_EXAM_STATUS로 다시 확인합니다. 원격으로 켠 시험은 새 연결에서 꺼지므로 자동 재활성화하지 않습니다. 단, B가 로컬 --exam-mode도 사용한 경우 그 시작 설정이 재적용됩니다.

### C-3. 프로세스 그룹·펼치기·일괄 종료

- [ ] 이름을 casefold해 같은 이름끼리 묶고 상위 행에 이름·개수·합산 메모리를 표시합니다. 펼치면 기존 개별 PID·사용자·메모리·보호 사유를 봅니다.
- [ ] 이름 null 항목을 하나의 종료 그룹으로 묶지 않습니다. 같은 이름이 같은 프로세스 트리/경로/사용자를 뜻하지 않는다는 점을 고려합니다.
- [ ] 완전히 수신한 최신 목록에서 can_stop=true이고 PID·생성 시각이 유효한 항목만 선택합니다. 중복 PID는 제거합니다.
- [ ] 확인창에 PC·그룹 이름·종료 대상 PID/개수·제외된 보호 항목 수를 표시합니다. 강제 종료에 따른 미저장 내용 손실과 브라우저 시험 창 종료 가능성을 안내합니다.
- [ ] 확인한 PID·생성 시각 목록을 고정해 STOP_PROCESSES로 보냅니다. 이름만 보내거나 확인 뒤 새로 발견한 대상을 추가하지 않습니다.
- [ ] 최대 100개를 넘으면 선택 범위를 줄이도록 안내합니다. 자동으로 여러 배치에 나눠 추가 종료하지 않습니다.
- [ ] PROCESS_BATCH_RESULT의 results를 개별 행에 표시하고 counts로 요약합니다. 최상위 ok=true는 처리 완료일 뿐 전체 종료 성공이 아닙니다.
- [ ] TERMINATION_REQUESTED는 종료 요청 상태로 표시하고 재조회합니다. 보호/실패/시간 제한 항목도 결과에 남깁니다. 목록을 다시 조회하고 임의 재시도하지 않습니다.

### C-4. RTT 표시

- [ ] PC별 '서버 RTT(ms)'를 예: 24.2 ms로 표시합니다.
- [ ] ok이며 값·시각이 유효할 때만 수치를 표시합니다. pending은 측정 대기, timeout은 응답 지연, stale/미지원/연결 해제는 `—`로 표시합니다.
- [ ] METRICS는 2초 주기이고 RTT는 기본 5초 주기임을 반영합니다. METRICS 갱신 시각만으로 RTT를 새 측정이라고 판단하지 않습니다.
- [ ] PC 시계 오차를 고려해 rtt_timestamp와 서버 연결/수신 상태를 함께 사용합니다. 서버 수신 시각을 통한 오래된 값 처리 방식은 A와 합의합니다.

## 5. 메시지 예시

아래 command_id·시간·PID·생성 시각은 예시입니다. C는 실제 전송 시 새 ID·만료 시각을 생성하고 조회한 실제 프로세스 식별값을 사용합니다. 모든 명령의 target_id는 대상 PC ID입니다.

### 5-1. 시험 모드 켜기

```json
{"type":"COMMAND","command_id":"unique-exam-on","target_id":"PC-02","action":"SET_EXAM_MODE","enabled":true,"exam_id":"midterm-2026","forbidden_processes":["Discord.exe","Telegram.exe"],"poll_interval":1,"expires_at":"2026-11-11T12:00:30Z"}
```

enabled는 JSON boolean입니다. 금지 이름은 경로/와일드카드 없이 전체 이름(최대 128자), 목록은 1~100개이며 대소문자를 구분하지 않습니다. poll_interval은 생략 시 1초, 범위는 0.5~60초입니다. 잘못된 설정은 기존 정책을 바꾸지 않습니다. 성공한 새 켜기는 같은 시험이라도 감지 이력을 초기화합니다.

```json
{"type":"COMMAND_RESULT","pc_id":"PC-02","command_id":"unique-exam-on","ok":true,"code":"EXAM_MODE_ENABLED","exam_mode":{"enabled":true,"exam_id":"midterm-2026","forbidden_processes":["discord.exe","telegram.exe"],"poll_interval":1,"monitor_status":"starting"}}
```

### 5-2. 시험 모드 끄기·조회

```json
{"type":"COMMAND","command_id":"unique-exam-off","target_id":"PC-02","action":"SET_EXAM_MODE","enabled":false,"exam_id":"midterm-2026","expires_at":"2026-11-11T12:00:30Z"}
```

```json
{"type":"COMMAND","command_id":"unique-exam-status","target_id":"PC-02","action":"GET_EXAM_STATUS","expires_at":"2026-11-11T12:00:30Z"}
```

성공 code는 끄기 EXAM_MODE_DISABLED, 조회 EXAM_STATUS입니다. exam_mode 구조는 같고 꺼짐은 enabled=false, forbidden_processes=[], poll_interval=null, monitor_status=off입니다. exam_id는 직전 시험 ID 또는 null입니다. 끄기에는 현재/직전 시험 ID가 일치해야 하며 불일치는 EXAM_ID_MISMATCH입니다. 한 번도 켜지 않은 새 연결에서는 조회로 꺼짐을 확인합니다.

켜기 성공 응답은 해당 연결의 첫 감지 이벤트보다 먼저 전송됩니다. 끄기 성공 응답 뒤에는 이전 시험의 감지 이벤트를 뒤늦게 전송하지 않습니다.

### 5-3. 시험 감지·감시 상태 이벤트

```json
{"type":"EXAM_EVENT","version":1,"event_id":"033be532-ac6f-4423-8be2-7ee749e8b43a","pc_id":"PC-02","exam_id":"midterm-2026","kind":"FORBIDDEN_PROCESS_DETECTED","timestamp":"2026-11-11T12:00:00Z","process":{"pid":12345,"name":"Discord.exe","create_time":1794190000.125}}
```

```json
{"type":"EXAM_EVENT","version":1,"event_id":"5de5a3a2-31c3-4f4d-a86c-a9c2099bb655","pc_id":"PC-02","exam_id":"midterm-2026","kind":"MONITOR_STATUS","timestamp":"2026-11-11T12:00:00Z","status":"partial","unreadable_count":2,"poll_interval":1}
```

시작 시 이미 실행 중인 금지 프로그램도 감지합니다. 같은 PID·생성 시각은 관찰 중 한 번 보고하고, 종료 관찰 뒤 재등장/PID 재사용/새 모드 시작 시 다시 보고할 수 있습니다. 감시 상태 이벤트는 처음과 상태 전환 시에만 보냅니다. 전체 열거 실패 시 unreadable_count는 전체 미확인 수를 보장하지 않습니다.

### 5-4. 일괄 종료

```json
{"type":"COMMAND","command_id":"unique-batch-stop","target_id":"PC-02","action":"STOP_PROCESSES","expires_at":"2026-11-11T12:00:30Z","targets":[{"pid":12345,"create_time":1794190000.125},{"pid":12346,"create_time":1794190001.25}]}
```

targets는 1~100개이며 PID는 중복 없는 양의 정수, create_time은 유한한 양수입니다. 하나라도 형식이 잘못되면 INVALID_TARGETS로 전체 거절하며 종료하지 않습니다. 이름 검색·트리 재귀 종료는 수행하지 않습니다.

```json
{"type":"COMMAND_RESULT","pc_id":"PC-02","command_id":"unique-batch-stop","ok":true,"code":"PROCESS_BATCH_RESULT","all_terminated":false,"counts":{"TERMINATED":1,"PROTECTED":1},"results":[{"pid":12345,"create_time":1794190000.125,"ok":true,"code":"TERMINATED"},{"pid":12346,"create_time":1794190001.25,"ok":false,"code":"PROTECTED","reason":"PROTECTED_NAME"}]}
```

결과는 요청 순서이며 최대 100행의 단일 응답입니다. all_terminated=true는 모든 대상의 종료를 확인한 경우에만 나옵니다. 보호 대상이 있어도 다른 대상은 처리합니다. 각 종료 확인 대기는 최대 0.1초이며, 처리 시작 후 5초 예산 초과 또는 명령 만료 시 남은 대상을 건너뜁니다. OS 호출 자체의 지연을 강제로 중단하는 보장은 아닙니다.

| 항목별 결과 | C 표시 의미 |
|---|---|
| TERMINATED | 종료 확인 |
| TERMINATION_REQUESTED | 종료 요청됨·재조회 필요 |
| PROTECTED + reason | 보호 정책으로 종료하지 않음 |
| PID_REUSED / NOT_FOUND | 대상 변경 / 이미 종료됨 |
| ACCESS_DENIED / PROCESS_ERROR | 권한 거절 / OS 오류 |
| EXPIRED_DURING_BATCH | 시작 전 명령 만료로 건너뜀 |
| BATCH_TIME_LIMIT | 시작 전 처리 시간 예산 초과로 건너뜀 |

### 5-5. RTT와 METRICS

```json
{"type":"RTT_PING","pc_id":"PC-02","probe_id":"ba62ee62-fab2-45a1-b579-fca39c101f17"}
```

```json
{"type":"RTT_PONG","pc_id":"PC-02","probe_id":"ba62ee62-fab2-45a1-b579-fca39c101f17"}
```

```json
{"type":"METRICS","pc_id":"PC-02","timestamp":"2026-11-11T12:00:02Z","cpu_percent":32.5,"ram_percent":48.1,"upload_mbps":0.2,"download_mbps":1.3,"rtt_ms":24.185,"rtt_status":"ok","rtt_timestamp":"2026-11-11T12:00:00Z"}
```

RTT는 기본 즉시 1회, 이후 약 5초마다 측정하고 3초 응답 초과 시 timeout/null입니다. 측정 전은 pending/null이며 rtt_timestamp도 null입니다. 새 요청 중에는 아직 유효한 이전 성공 값을 유지할 수 있습니다. 최근 성공 후 interval+timeout보다 오래된 값은 stale/null로 바꿉니다. 재접속 시 값·요청 ID를 초기화합니다.

RTT에는 네트워크·TCP/TLS·서버 응답·Agent 수신 스레드 대기가 포함됩니다. ICMP ping과 같지 않으며 긴 프로세스 명령 처리 중에는 지연/타임아웃이 생길 수 있습니다.

### 5-6. 공통 거절과 UI 처리

| code | 의미·대응 |
|---|---|
| PROCESS_CONTROL_DISABLED | Agent에서 프로세스 조회/종료 허용 옵션 필요 |
| EXAM_CONTROL_DISABLED | Agent에서 원격 시험 제어 허용 옵션 필요 |
| INVALID_EXAM_SETTINGS / EXAM_ID_MISMATCH | 설정 수정 / 실제 시험 상태 재조회 |
| INVALID_TARGETS | 목록·선택 대상을 다시 확인 |
| INVALID_COMMAND_ID / WRONG_TARGET / INVALID_EXPIRY | 요청 생성·대상·시간 형식 수정 |
| EXPIRED_OR_TOO_FAR | 현재 시각과 만료 시각 확인 |
| DUPLICATE_COMMAND | 동일 명령을 재실행하지 않고 상태 재조회 |
| COMMAND_CAPACITY | 중복 방지 캐시 한도; 자동 반복 전송 금지 |
| UNSUPPORTED_ACTION | A 서버·B Agent 버전/허용 목록 확인 |
| TARGET_OFFLINE / TLS_REQUIRED | 서버 측 연결·인증 조건 확인 |

실패 COMMAND_RESULT는 일반적으로 ok=false와 code를 포함합니다. 성공 때의 exam_mode/results가 반드시 있을 것으로 가정하지 않습니다.

## 6. Agent 실행 조건과 B 측 남은 설정

| 기능 | Agent 조건 |
|---|---|
| CPU·RAM·네트워크 전송 | 기존 ID·토큰·인증서와 --live |
| 프로세스 조회·개별/일괄 종료 | 기존 TLS·토큰·--live + --enable-process-control |
| GUI 시험 켜기·끄기·조회 | 기존 TLS·토큰·--live + --allow-remote-exam-control |
| 서버 RTT | 최신 B 코드 + 서버 rtt_v1:true; 기본 자동 활성 |

- PC-01·03 실행 파일은 프로세스 제어와 원격 시험 제어 옵션이 모두 포함되어 있습니다.
- PC-02의 `Start PC-02 Exam Control.cmd`도 --enable-process-control과 --allow-remote-exam-control을 모두 포함하도록 보완했습니다. PC-01·03과 같은 인증서·토큰 대체 경로 검사와 실행 전 확인 대기도 적용했습니다. 이미 실행 중인 Agent에는 자동 반영되지 않으므로 기존 Agent를 종료한 뒤 보완된 파일로 재실행해야 합니다.
- 원격 시험 제어 허용은 감지 즉시 시작을 뜻하지 않습니다. 로컬 --exam-mode 없이 시작하면 모드는 꺼진 상태로 대기합니다.
- 기존 인증서·토큰을 교체하지 않습니다. 각 PC의 자기 ID·토큰을 사용하고 인증정보를 PC끼리 복사하지 않습니다.
- 서버 중계·GUI 통합 구현과 관계없이 실행 파일만 업데이트했다고 전체 기능 완료로 표시하지 않습니다.

## 7. 권장 통합·병합 순서

1. A·C가 이 문서의 메시지 이름·필드·실패 처리와 사용할 B 커밋을 확인합니다. RTT 변경도 먼저 공유된 버전으로 맞춥니다.
2. A·C가 각자 작업 브랜치에서 구현합니다. 규격 변경이 필요하면 B와 먼저 합의합니다.
3. 임시 통합 브랜치에 A·B·C 변경을 모아 기존 테스트와 각 담당의 새 테스트를 실행합니다.
4. A가 실제 수신/중계/응답이 가능한 서버 버전을 준비합니다. 새 시험 허용 실행 파일을 사용할 때는 서버 지원이 선행되어야 합니다.
5. 각 담당자가 PC 3대의 최신 Agent·보완된 실행 파일·기존 인증정보로 접속합니다. 실행 중인 이전 Agent는 먼저 종료해 중복 접속하지 않습니다.
6. 아래 공동 검증을 수행하고 시각·버전·PC·결과·화면/로그를 기록합니다.
7. 검증된 변경을 리뷰한 뒤 main에 병합하고 배포 버전과 실행 방법을 팀에 알립니다. 실제 배포는 A 담당입니다.

## 8. 공동 검증 완료 기준

| 시나리오 | 기대 결과 | 확인 |
|---|---|---|
| 3대 동시 접속 | 서로 다른 ID·CPU·RAM·연결 상태가 한 화면에 표시 | [ ] |
| 각 PC 부하 변화 | 해당 PC의 값만 변화하고 다른 PC와 뒤바뀌지 않음 | [ ] |
| 모드 켜기→감지 | 성공 응답 후 감시 상태 및 금지 프로그램 경고 수신 | [ ] |
| 이미 실행 중인 금지 앱 | 모드 시작 직후 감지 | [ ] |
| 같은 프로세스 반복 검사 | 불필요한 중복 경고 없음 | [ ] |
| 모드 끄기 | 꺼짐 확인 뒤 이전 시험의 새 감지 전송 중단 | [ ] |
| 모드 재접속 | 기존 상태 무효화·조회·원격 시험 꺼짐 확인 | [ ] |
| 정보 접근 불가 | partial/unavailable과 감지 이력을 구분 | [ ] |
| 그룹 펼치기 | 개별 PID·보호 사유 확인 가능 | [ ] |
| 일괄 종료 | 자체 생성한 테스트 프로세스만 선택 종료, 개별 결과 표시 | [ ] |
| 보호/이미 종료/PID 변경 | 해당 대상 거절, 나머지 결과 정상 처리 | [ ] |
| 중복/만료/잘못된 대상 | 실행되지 않고 정해진 거절 코드 반환 | [ ] |
| RTT 정상 | PC별 ms 표시·METRICS에 결과 포함 | [ ] |
| RTT 응답 지연 | timeout/null, CPU/RAM 전송은 유지 | [ ] |
| 네트워크 단절·복구/서버 재시작 | 온라인 상태 갱신·같은 ID 재접속·오래된 값 숨김 | [ ] |

테스트 종료 대상은 각 PC 소유자가 승인한 자체 생성 테스트 프로세스를 사용합니다. 실제 사용자 앱을 임의로 종료하지 않습니다. 시험 모드는 이름 기반 주기 감지이므로 짧게 실행 후 종료·이름 변경·브라우저 다른 탭/사이트까지 감지하는 기능으로 설명하지 않습니다.

## 9. B 검증 근거와 세부 문서

아래 관련 테스트 **63개 통과**를 확인했습니다. Agent 소켓 통신은 로컬 테스트이며 실제 A 서버·C GUI 전체 검증을 대체하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_rtt tests.test_process_batch tests.test_processes tests.test_exam_commands tests.test_exam tests.test_heartbeat tests.test_agent tests.test_agent_security -q
```

- [B 기본 프로세스 조회·보호 정책](process-control.md)
- [B 시험 모드 상세](b-exam-mode-handoff.md)
- [B 일괄 종료 상세](b-batch-process-handoff.md)
- [B RTT 상세](b-rtt-handoff.md)
- [B 2주차 결과와 실제 PC 공동 검증](week2-b-handoff.md)

충돌하거나 추가 규격이 필요하면 구현 코드와 이 문서를 함께 확인하고 담당자끼리 합의한 뒤 문서를 갱신합니다.
