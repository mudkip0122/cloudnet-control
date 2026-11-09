# CloudNet 통신 규격 — 서버 2주차

프로세스 조회·보호 종료 확장(2026-11-09)의 COMMAND/COMMAND_RESULT 필드와 TLS 활성화 조건은 [프로세스 제어 규격](process-control.md)을 참조하세요. 아래 로컬 서버에는 해당 명령 라우팅을 추가하지 않았습니다.

## 구현 범위

Agent/Admin 등록·인증, TLS, 중복 ID 거절, 측정값 수신, 관리자 목록 조회, Heartbeat와 선택적 상태 알림을 지원합니다. 인증 없는 평문 연결은 루프백 개발에만 허용합니다. 외부 바인딩은 TLS 인증서·개인키·인증 파일을 모두 요구합니다. 실행 방법은 [EC2 안내](ec2-direct.md)를 참고하세요.

## 공통 프레이밍

- UTF-8 JSON 객체 앞에 본문 바이트 수를 나타내는 4바이트 unsigned 정수를 붙입니다.
- 헤더는 big-endian(network byte order, Python `!I`)입니다.
- 본문은 1~65,536바이트이며 헤더 길이는 포함하지 않습니다.
- 모든 메시지는 문자열 `type`이 필요합니다. NaN·Infinity는 허용하지 않습니다.
- `common.protocol.send_message(sock, message)`와 `recv_message(sock)`를 함께 사용합니다.
- 연결 종료는 `EOFError`, 잘못된 규격은 `ValueError`, 시간 초과·네트워크 오류는 `OSError` 계열입니다.
- 서버는 연결별 송신 잠금으로 전체 프레임을 직렬화합니다. 관리자 상태 알림은 연결별 최대 32개 큐를 사용하며, 큐가 가득 차거나 송신에 실패하면 해당 관리자 연결을 종료합니다.

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

TLS/인증 모드에서는 REGISTER에 `token`을 포함합니다. Agent 토큰은 서버의 `agents[pc_id]`, 관리자 토큰은 `admin_token`과 비교합니다. 토큰 누락·불일치·역할 간 재사용은 거절합니다. TLS 클라이언트는 신뢰 인증서와 서버 이름을 검증합니다.

서버는 등록 읽기에 5초, 등록 이후 유효 메시지 수신에 기본 15초 제한을 둡니다. `--heartbeat-timeout`으로 등록 이후 제한을 변경할 수 있습니다. 완성되지 않은 메시지의 일부 바이트만 보내서는 생존 시간이 연장되지 않습니다.

Agent는 5초마다 `{"type":"HEARTBEAT","pc_id":"example-pc-b-001"}`을 보낼 수 있습니다. 서버는 등록 ID 일치를 검사하고 최근 수신 시각을 갱신하며 별도 ACK는 보내지 않습니다. 기존 Agent의 2초 간격 METRICS도 생존 확인으로 인정하므로 기존 B 코드 수정은 필수가 아닙니다. 타임아웃·연결 종료 시 해당 PC만 오프라인 처리하고 세션 ID를 해제합니다.

## 현재 관리자 조회 흐름

EC2 TLS 모드에서는 Agent와 관리자 모두 인증서·서버 이름을 검증하고 REGISTER에 각 역할의 토큰을 포함합니다. 루프백 개발 모드에서만 TLS·토큰 없이 등록할 수 있습니다.

관리자는 `{"type":"REGISTER","role":"admin"}`으로 등록하고 `{"type":"REGISTER_RESULT","ok":true}`를 받습니다. 이후 1초마다 `{"type":"LIST_DEVICES"}`를 요청합니다.

응답 예시:

```json
{"type":"DEVICE_LIST","devices":[{"pc_id":"example-pc-b-001","pc_name":"TEAM-B-PC","online":true,"last_received":"2026-10-30T03:00:00Z","metrics":null}]}
```

`metrics`는 첫 수신 전 null, 수신 후 [공통 측정 규격](metrics-spec.md)의 METRICS 객체입니다. Agent는 등록한 ID와 동일한 ID로 측정값을 보내야 합니다. 연결 종료·읽기 타임아웃은 `online:false`로 반영됩니다. 오프라인 기록은 서버 메모리에 남고 서버 재시작 시 초기화됩니다. 현재 목록은 한 프레임으로 보내므로 소규모 로컬 테스트용이며 대규모 목록 페이지 처리는 없습니다.

## 관리자 상태 알림 구독

관리자가 REGISTER에 `"subscribe_status":true`를 넣으면 등록 성공 응답 이후 접속·해제·타임아웃 변경을 받습니다. 기본값은 false이므로 기존 GUI의 LIST_DEVICES 주기 조회는 그대로 동작합니다.

```json
{"type":"STATUS_UPDATE","device":{"pc_id":"example-pc-b-001","pc_name":"TEAM-B-PC","online":false,"last_received":"2026-10-30T03:00:00Z","metrics":null},"timestamp":"2026-10-30T03:00:15Z"}
```

구독 클라이언트는 DEVICE_LIST와 STATUS_UPDATE가 번갈아 도착할 수 있으므로 type별로 처리해야 합니다. 접속 직후 전체 상태는 LIST_DEVICES로 조회합니다. Heartbeat마다 상태 알림을 보내지는 않습니다. 관리자만 LIST_DEVICES와 상태 구독을 사용할 수 있고, Agent만 자신의 METRICS·HEARTBEAT를 보낼 수 있습니다. 다른 요청은 ERROR 후 연결을 종료합니다.

## 메시지 요약

| 흐름 | 제안 메시지 | 필요한 정보 |
|---|---|---|
| 관리자 인증·등록 | `REGISTER` / `REGISTER_RESULT` | 역할, 별도 관리자 인증 방식 |
| 초기 목록 요청·응답 | `LIST_DEVICES` / `DEVICE_LIST` | PC ID·이름·온라인 여부·최근 수신 시각 |
| 상태 변경 | `STATUS_UPDATE` | device 객체·판정 시각, 관리자 선택 구독 |
| 측정값 중계 | `METRICS` | [공통 측정 규격](metrics-spec.md)의 필드 |
| 연결 생존 확인 | `HEARTBEAT` | 등록 PC ID, 권장 5초 주기·기본 15초 타임아웃 |

위 흐름은 서버에 구현되어 있습니다. 현재 GUI는 호환성을 위해 목록 주기 조회를 유지합니다. 이력 조회·원격 명령은 2주차 범위에 포함하지 않습니다.

## Agent Heartbeat ???

?? Agent? ??? 0?? ?? Heartbeat? ???. ?? ???? `--heartbeat-interval 5`? ???? ? ????. ??? REGISTER_RESULT? heartbeat_interval? ???? ?? ?? CLI?? ???? ????? ?? ?????. ?? ??? ? ??? ??? ??? ACK? ??? ????. ?? 2? METRICS???? ?? 15? ?? ??? ?????.
