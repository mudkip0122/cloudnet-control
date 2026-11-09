# 1주차 로컬 통신 규격

프로세스 조회·보호 종료 확장(2026-11-09)의 COMMAND/COMMAND_RESULT 필드와 TLS 활성화 조건은 [프로세스 제어 규격](process-control.md)을 참조하세요. 현재 서버는 TLS 관리자 명령 중계를 지원하며 GUI 연동·실행 조건은 [프로세스 GUI 안내](gui-process-control.md)에 정리했습니다.

## 구현 범위

기본 실행은 `127.0.0.1`의 인증 없는 로컬 모니터링입니다. 서버·GUI·Agent 모두 선택적 TLS·역할별 토큰 인증을 지원하며 원격 접속은 인증 옵션이 필요합니다. 프로세스 명령 중계는 인증된 TLS 세션에서만 허용합니다.

## 공통 프레이밍

- UTF-8 JSON 객체 앞에 본문 바이트 수를 나타내는 4바이트 unsigned 정수를 붙입니다.
- 헤더는 big-endian(network byte order, Python `!I`)입니다.
- 본문은 1~65,536바이트이며 헤더 길이는 포함하지 않습니다.
- 모든 메시지는 문자열 `type`이 필요합니다. NaN·Infinity는 허용하지 않습니다.
- `common.protocol.send_message(sock, message)`와 `recv_message(sock)`를 함께 사용합니다.
- 연결 종료는 `EOFError`, 잘못된 규격은 `ValueError`, 시간 초과·네트워크 오류는 `OSError` 계열입니다.
- Agent의 METRICS·HEARTBEAT 송신은 연결별 잠금으로 직렬화합니다. 등록은 작업 스레드 시작 전에 수행하며 수신은 전용 스레드 하나가 담당합니다.

## 현재 실행 가능한 등록 흐름

Agent → 서버:

```json
{"type":"REGISTER","role":"agent","pc_id":"example-pc-b-001","pc_name":"TEAM-B-PC"}
```

`pc_id`와 `pc_name`은 공백뿐이지 않은 1~128자 문자열입니다. Agent는 `--pc-id`를 생략하면 `runtime/agent-id.json`에 UUID를 생성·저장하고 재실행할 때도 같은 값을 전달합니다. 수동 ID는 `--pc-id`로 지정하며 저장된 ID를 덮어쓰지 않습니다.

서버 → Agent (응답 후 연결 유지):

```json
{"type":"REGISTER_RESULT","ok":true,"pc_id":"example-pc-b-001"}
```

규격 오류 시 가능한 경우 다음 응답을 보내고 닫습니다. 메시지 미완성·연결 단절·시간 초과 시에는 응답 없이 닫을 수 있습니다.

```json
{"type":"ERROR","code":"INVALID_MESSAGE","message":"First message must be REGISTER"}
```

등록 성공은 입력 형식 확인만 의미하며 사용자 인증 성공을 뜻하지 않습니다. 서버는 등록 읽기에 5초, 등록 이후 메시지 읽기에 15초 제한을 둡니다. Agent는 2초마다 METRICS를 보내며 이 단계에서는 별도 Heartbeat 없이 측정값이 연결을 유지합니다.

## 현재 관리자 조회 흐름

TLS 모드의 Agent·GUI는 인증서·서버 이름을 검증하고 `REGISTER`에 해당 역할의 `token`을 추가합니다. 서버는 관리자·Agent 토큰을 구분해 검증하며 아래 기본 로컬 개발 흐름에서는 인증 옵션을 생략합니다.

관리자는 `{"type":"REGISTER","role":"admin"}`으로 등록하고 `{"type":"REGISTER_RESULT","ok":true}`를 받습니다. 이후 1초마다 `{"type":"LIST_DEVICES"}`를 요청합니다.

응답 예시:

```json
{"type":"DEVICE_LIST","devices":[{"pc_id":"example-pc-b-001","pc_name":"TEAM-B-PC","online":true,"last_received":"2026-10-30T03:00:00Z","metrics":null}]}
```

`metrics`는 첫 수신 전 null, 수신 후 [공통 측정 규격](metrics-spec.md)의 METRICS 객체입니다. Agent는 등록한 ID와 동일한 ID로 측정값을 보내야 합니다. 연결 종료·읽기 타임아웃은 `online:false`로 반영됩니다. 오프라인 기록은 서버 메모리에 남고 서버 재시작 시 초기화됩니다. 현재 목록은 한 프레임으로 보내므로 소규모 로컬 테스트용이며 대규모 목록 페이지 처리는 없습니다.

## 이후 확장 검토안

| 흐름 | 제안 메시지 | 필요한 정보 |
|---|---|---|
| 관리자 인증·등록 | `REGISTER` / `REGISTER_RESULT` | 역할, 별도 관리자 인증 방식 |
| 초기 목록 요청·응답 | `LIST_DEVICES` / `DEVICE_LIST` | PC ID·이름·온라인 여부·최근 수신 시각 |
| 상태 변경 | `STATUS_UPDATE` | PC ID·온라인 여부·판정 시각 |
| 측정값 중계 | `METRICS` | [공통 측정 규격](metrics-spec.md)의 필드 |
| 연결 생존 확인 | `HEARTBEAT` | 5초 주기·15초 타임아웃 초안 |

위 표의 목록 조회·측정값 수신과 TLS 관리자 인증은 구현되어 있습니다. STATUS_UPDATE push는 미구현이며 GUI는 전체 목록 주기 조회로 상태 변화를 받습니다. 서버는 Agent의 선택적 HEARTBEAT 수신·ACK를 지원합니다.

## B 담당 2주차 Heartbeat 계약 (2026-11-09)

아래는 Agent에서 구현하고 임시 TLS 서버로 검증한 계약입니다. 배포 EC2 서버의 지원 여부는 A와 별도로 확인합니다.

서버가 등록 응답에 `heartbeat_interval`을 포함하면 해당 주기를 사용합니다.

```json
{"type":"REGISTER_RESULT","ok":true,"pc_id":"PC-02","heartbeat_interval":5}
```

Agent → 서버 (등록 후 5초 간격, 측정 수집과 독립):

```json
{"type":"HEARTBEAT","pc_id":"PC-02","timestamp":"2026-11-09T05:00:00Z"}
```

선택적 서버 → Agent 응답:

```json
{"type":"HEARTBEAT_ACK","pc_id":"PC-02"}
```

간격은 0(비활성) 또는 1~60초의 유한 숫자입니다. 필드가 없으면 Agent CLI 설정을 사용하며 기본값은 0입니다. A의 권장 서버 정책은 5초 전송·15초 미수신 시 오프라인입니다. 인증 세션과 PC ID 일치 검증, 타임아웃 판정·이벤트 기록은 서버 책임입니다. 기존 METRICS만 받는 서버에는 Heartbeat를 켜지 않습니다. ACK가 없어도 연결을 유지하며 ACK 지연에 의한 별도 생존 판정은 하지 않습니다.
