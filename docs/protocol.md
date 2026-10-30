# 1주차 로컬 통신 규격

## 구현 범위

현재 코드는 `127.0.0.1`에서 Agent/Admin 등록, 세션 유지, 중복 접속 ID 거절, 측정값 수신과 관리자 목록 조회를 지원합니다. 인증·TLS는 아직 없습니다. EC2 또는 다른 PC에 공개하기 전 TLS와 Agent/Admin 인증을 구현해야 합니다.

## 공통 프레이밍

- UTF-8 JSON 객체 앞에 본문 바이트 수를 나타내는 4바이트 unsigned 정수를 붙입니다.
- 헤더는 big-endian(network byte order, Python `!I`)입니다.
- 본문은 1~65,536바이트이며 헤더 길이는 포함하지 않습니다.
- 모든 메시지는 문자열 `type`이 필요합니다. NaN·Infinity는 허용하지 않습니다.
- `common.protocol.send_message(sock, message)`와 `recv_message(sock)`를 함께 사용합니다.
- 연결 종료는 `EOFError`, 잘못된 규격은 `ValueError`, 시간 초과·네트워크 오류는 `OSError` 계열입니다.
- 현재는 연결별 순차 송수신입니다. 이후 여러 스레드가 같은 연결로 보내면 연결별 송신 잠금이 필요합니다.

## 현재 실행 가능한 등록 흐름

Agent → 서버:

```json
{"type":"REGISTER","role":"agent","pc_id":"example-pc-b-001","pc_name":"TEAM-B-PC"}
```

`pc_id`와 `pc_name`은 공백뿐이지 않은 1~128자 문자열입니다. 각 PC의 고유 ID는 재실행할 때도 같은 값을 전달합니다. 현재 프로그램은 ID를 생성·저장하지 않으므로 팀에서 정한 실제 값을 `--pc-id`로 지정합니다.

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

위 표의 목록 조회·측정값 수신은 앞 절대로 구현되어 있습니다. 관리자 인증·STATUS_UPDATE push·HEARTBEAT는 미구현이며 현재 GUI는 목록 주기 조회로 상태 변화를 받습니다.
