# A→C 시험·일괄 종료·RTT 연동 규격 v1

2026-11-12. A 작업 브랜치: `integration/a-exam-batch-control`.
B 원 규격: `3a091ef`. 현재 통합 B: `57b530b`, C: `35004c3`.
A의 구독·공유 상태·시험 지원 플래그 구현 커밋: `c5ecfef`.

후속 B `b6f76c0`의 Chrome 이탈/복귀·활성 창 감시 상태 이벤트와 `exam_foreground_v1`은 A `18139f1`에 구현되어 있습니다. C 최신 `b257588`의 새 kind 처리도 통합했습니다. [A 전경 감시 연동](a-foreground-integration.md)에 최신 검증 및 공동 시연의 남은 범위를 기록합니다.

이 문서는 C의 연동 요청에 따라 A가 지원하는 서버 형식입니다. B의 COMMAND·COMMAND_RESULT·EXAM_EVENT·METRICS 필드는 변경하지 않습니다. A는 B Agent/C GUI 소스를 직접 수정하지 않았습니다. 통합 브랜치의 지원이며 운영 EC2 배포 완료를 의미하지 않습니다.

## 1. 명령과 결과

`SET_EXAM_MODE`, `GET_EXAM_STATUS`, `STOP_PROCESSES`는 [B 인계 규격](A-C-integration-todo.md) 그대로 COMMAND로 요청하고 COMMAND_RESULT 한 개를 받습니다. 기존 LIST_PROCESSES만 여러 페이지입니다.

- TLS 관리자 인증, PC별 target_id, 고유 command_id, 현재 시각 이후 60초 이내 expires_at 필요.
- 결과는 요청한 관리자 연결에만 반환합니다. pc_id·command_id를 대조합니다.
- STOP_PROCESSES의 확인한 targets 목록·순서·PID/생성 시각을 유지합니다. 최상위 ok=true는 모든 대상 종료 성공을 뜻하지 않습니다.
- 결과 미수신은 미확인입니다. 모드 변경/종료를 자동 재전송하지 않습니다.

시험 켜기 요청의 `forbidden_processes`는 선택 필드입니다. C GUI에서 공란/공백이면 필드를 생략하고 A는 그대로 B에 전달해 B 기본 목록을 적용합니다. 명시한 1~100개 목록은 기본 목록을 대체하며 A가 합치거나 기본값을 주입하지 않습니다. 명시적 빈 배열 `[]`·`null`·잘못된 목록은 INVALID_EXAM_SETTINGS로 거절합니다. 검사 간격 검증은 생략 여부와 관계없이 유지합니다. 성공 결과/GET_EXAM_STATUS의 exam_mode에는 B가 확정한 실제 forbidden_processes 목록이 포함되어야 합니다.

## 2. 관리자 구독

기존 목록 폴링 연결은 그대로 사용해도 됩니다. 시험 알림용 **별도 TLS 관리자 연결**에서 아래 REGISTER를 보냅니다. token은 기존 관리자 토큰이며 예시는 자리표시자입니다.

```json
{"type":"REGISTER","role":"admin","token":"<existing-admin-token>","subscribe_exam":true,"subscribe_status":true}
```

두 구독 필드의 기본값은 false이며 반드시 JSON boolean입니다. Agent는 구독할 수 없습니다. 시험 구독은 TLS·관리자 인증·감사 저장소가 준비된 서버에서만 허용합니다. 현재 관리자 공통 토큰의 감독자는 동일 권한으로 모든 연결 PC의 시험 이벤트를 받습니다. 시험별/개인별 권한 분리 기능은 없습니다.

등록 응답:

```json
{"type":"REGISTER_RESULT","ok":true,"exam_events_v1":true,"exam_control_v1":true}
```

등록 후 LIST_DEVICES를 보내 초기 상태를 조회합니다. 구독 연결에서도 **최대 5초마다 LIST_DEVICES를 보내** 서버 기본 15초 유휴 제한을 넘기지 않습니다. 서버가 알림을 보낸 것만으로 관리자 수신 활동 시각이 갱신되지는 않습니다. 별도 관리자 HEARTBEAT 메시지는 추가하지 않았습니다.

수신 스레드는 DEVICE_LIST·STATUS_UPDATE·EXAM_EVENT·COMMAND_RESULT를 type별로 분기합니다. 명령을 이 연결에서 보낼 경우 결과와 알림이 섞일 수 있습니다. 모든 송신은 연결별 잠금으로 프레임을 보호합니다. C는 이벤트/명령 결과를 최신값 하나만 남기는 큐에서 버리지 않아야 합니다.

## 3. 시험 이벤트

subscribe_exam:true인 관리자에게 검증·저장된 B의 EXAM_EVENT를 **동일한 최상위 필드로** 보냅니다. 래퍼나 별도 ACK를 추가하지 않습니다.

```json
{"type":"EXAM_EVENT","version":1,"event_id":"example-unique-event","pc_id":"PC-02","exam_id":"midterm-2026","kind":"FORBIDDEN_PROCESS_DETECTED","timestamp":"2026-11-12T03:00:00Z","process":{"pid":12345,"name":"Discord.exe","create_time":1794190000.125}}
```

MONITOR_STATUS의 status·unreadable_count·poll_interval도 B 규격 그대로입니다. event_id 중복은 서버에서 저장·실시간 중계를 한 번만 처리하며 C도 중복 표시를 막습니다. 서버 수신 시각은 DB에 별도 저장하고 원문 timestamp를 덮어쓰지 않습니다.

실시간 구독은 등록 이후 이벤트만 전달합니다. 서버 DB 보존과 GUI 재접속 후 이벤트 재전송은 별개이며, 현재 이력 조회/재전송 API는 없습니다. 관리자 큐 32개가 가득 차면 해당 연결을 닫습니다. C는 알림 연결 단절·감시 공백을 표시하고 재접속 후 상태를 재조회합니다. 이벤트가 없다는 이유로 감시 정상이나 금지 앱 없음으로 확정하지 않습니다.

별도 명령 연결과 구독 연결 사이의 전체 수신 순서는 보장하지 않습니다. 이미 전송 큐에 들어간 감지 이벤트가 끄기 응답보다 늦게 보일 수 있습니다. 이벤트는 감지 이력으로 보존하고, 현재 모드 상태는 COMMAND_RESULT·STATUS_UPDATE·DEVICE_LIST로 확인합니다. EXAM_EVENT 수신만으로 꺼진 모드를 다시 켜진 상태로 바꾸지 않습니다.

## 4. 다른 관리자 변경·Agent 재접속

subscribe_status:true이면 다음 시점에 기존 STATUS_UPDATE를 받습니다.

- Agent 등록·연결 해제·Heartbeat 만료.
- SET_EXAM_MODE 또는 GET_EXAM_STATUS 성공 결과 확인.
- 확인된 같은 시험의 MONITOR_STATUS 전환.

DEVICE_LIST.devices[]와 STATUS_UPDATE.device는 같은 PC 구조를 사용합니다. 아래는 핵심 필드만 표시한 예입니다.

```json
{"type":"STATUS_UPDATE","timestamp":"2026-11-12T03:00:01Z","server_timestamp":"2026-11-12T03:00:01Z","device":{"pc_id":"PC-02","pc_name":"PC-02","online":true,"agent_session_id":"connection-unique-id","exam_state_received_at":"2026-11-12T03:00:01Z","exam_mode":{"enabled":true,"exam_id":"midterm-2026","forbidden_processes":["discord.exe"],"poll_interval":1,"monitor_status":"starting"}}}
```

- exam_mode는 B의 성공 결과와 같은 객체입니다. MONITOR_STATUS가 오면 같은 시험의 monitor_status만 갱신합니다.
- exam_mode:null은 **미확인**이며 꺼짐과 다릅니다. Agent 단절·새 등록에서 null로 무효화하고 exam_state_received_at도 null로 만듭니다.
- agent_session_id는 서버가 등록마다 새로 생성하는 식별값입니다. C가 오프라인 알림을 놓쳤어도 이 값의 변경으로 새 연결을 알아낼 수 있습니다.
- C는 새 연결에서 GET_EXAM_STATUS로 실제 모드를 다시 확인합니다. 자동 재활성화하지 않습니다. B의 로컬 시험 시작 옵션은 별도로 재적용될 수 있습니다.
- 명령 실패는 기존 모드 상태를 바꾸지 않습니다. 요청자가 먼저 끊겨도 유효한 Agent 결과는 감사 저장·다른 감독자 상태 갱신에 반영합니다.
- exam_state_received_at은 마지막 확인 시각이며 주기적인 감시 정상 보증 시각이 아닙니다. B는 감시 상태 전환 때만 이벤트를 보냅니다.

## 5. RTT와 서버 수신 시각

각 PC의 metrics에는 B가 보낸 rtt_ms·rtt_status·rtt_timestamp를 그대로 유지합니다. 서버는 RTT_PING에 같은 PC ID·probe_id의 RTT_PONG을 즉시 응답하며 관리자에게 PING을 중계하지 않습니다.

DEVICE_LIST.devices[]의 각 PC에 아래 필드를 제공합니다. 기존 DEVICE_LIST의 최상위 구조는 유지합니다. STATUS_UPDATE.device도 같은 필드를 사용하며 STATUS_UPDATE 최상위에도 server_timestamp를 제공합니다.

| 필드 | 의미 |
|---|---|
| server_timestamp | 이 PC 스냅샷을 생성한 서버 UTC 시각 |
| last_received | 등록/Heartbeat/METRICS의 최근 서버 수신 UTC 시각. 측정 신선도 판단용으로 사용하지 않음 |
| last_metrics_received | 최신 METRICS의 서버 수신 UTC 시각. 미수신은 null |
| metrics_age_seconds | 서버 단조 시계 기준 METRICS 수신 후 경과 초. 미수신은 null |
| metrics_stale | 오프라인·미수신 또는 수신 후 6초 초과이면 true |
| last_rtt_received | 현재 rtt_timestamp를 처음 수신한 서버 UTC 시각. 미지원/미수신은 null |
| rtt_age_seconds | 그 RTT 시각을 처음 수신한 뒤 서버 단조 시계 기준 경과 초. 미수신은 null |
| rtt_stale | 측정값이 stale이거나 RTT 미수신·ok 아님·동일 RTT 수신 후 8초 초과이면 true |

같은 rtt_timestamp가 다음 METRICS에서 반복되어도 last_rtt_received는 갱신하지 않습니다. RTT 기본 5초 주기+3초 timeout에 맞춘 표시 기준은 8초이며, B의 측정 주기를 바꾸려면 표시 기준도 함께 조정해야 합니다. 재접속하면 관련 캐시를 초기화합니다.

C는 연결됨·online·metrics_stale=false·rtt_stale=false·rtt_status=ok·유효한 숫자인 경우에만 RTT 수치를 표시합니다. pending/timeout/stale/null은 B 규격대로 구분합니다. 서버 응답을 받은 뒤 흐른 시간은 C의 단조 시계로 더해 장시간 멈춘 응답의 값을 계속 표시하지 않습니다. Agent/서버/GUI의 UTC 시각을 서로 빼서 RTT나 수신 경과 시간을 계산하지 않습니다. rtt_timestamp는 독립 측정 식별·원문 시각으로 보존합니다.

## 6. 지원 플래그와 통합 순서

TLS·인증·감사 저장소가 준비된 Agent 등록 응답에는 heartbeat_interval(기본 5), exam_events_v1:true, exam_control_v1:true, rtt_v1:true를 보냅니다. 운영 인증정보를 교체할 필요는 없습니다.

A 통합 브랜치를 C 작업 브랜치에 가져와 C 담당자가 구독·경고·상태·신선도 처리를 연결합니다. 이후 통합 브랜치에서 로컬 테스트, EC2 배포, 실제 PC 3대 공동 검증을 진행합니다. 검증·리뷰 전 main에 병합하지 않습니다. 자체 생성하고 각 PC 소유자가 승인한 테스트 프로세스만 종료합니다.

2026-11-12 검증: 전체 162개 통과, skip 없음. 새 A TLS 테스트 8개에는 B의 수정 없는 stream을 사용한 시험 켜기/감지/끄기/조회와 RTT가 포함됩니다. C GUI 소스는 수정하지 않았으며 실제 PC 3대/운영 EC2 시연은 별도입니다.

후속: C `35004c3`가 제안에 동의하고 구독·이력을 구현해 A 통합 브랜치에 병합했습니다. C 합의 문서의 A `ef3f64e` 미구현 안내는 과거 버전 설명이며 `c5ecfef`부터 해당 서버 기능이 구현되어 있습니다. 최신 통합 검증 범위는 [공동 검증 준비 기록](a-c-week3-validation.md)을 참고합니다.
