# 관리자 시험·전원 감사 이력 조회 v1

기존 측정·경고 `GET_HISTORY`는 그대로 사용한다. 인증된 관리자 TLS 연결의 `REGISTER_RESULT`에 `audit_history_v1: true`가 있으면 다음 요청을 사용할 수 있다.

```json
{"type":"GET_AUDIT_HISTORY","version":1,"request_id":"audit-1","pc_id":"PC-02","kind":"exam","start_at":"2026-11-19T07:00:00Z","end_at":"2026-11-19T08:00:00Z"}
```

`kind`는 `exam` 또는 `power`다. 시간대가 있는 ISO 8601 시각을 사용한다. 기간은 서버 수신 시각 기준으로 시작 포함·끝 제외이며 최대 30일이다. 시험의 발생 시각은 `event.timestamp`, 수신 시각은 `received_at`이다. 전원 명령은 요청 수신 시각으로 필터링하며, 그 요청의 이후 단계도 함께 반환한다. PC가 오프라인이어도 조회할 수 있다.

```json
{"type":"AUDIT_HISTORY_RESULT","version":1,"request_id":"audit-1","pc_id":"PC-02","kind":"exam","ok":true,"page":0,"done":true,"rows":[{"received_at":"2026-11-19T07:30:01Z","event":{"type":"EXAM_EVENT","version":1,"event_id":"evt-1","pc_id":"PC-02","exam_id":"exam-1","kind":"EXAM_TAB_CHANGED","timestamp":"2026-11-19T07:30:00Z","transition":"left"}}]}
```

```json
{"type":"AUDIT_HISTORY_RESULT","version":1,"request_id":"audit-2","pc_id":"PC-02","kind":"power","ok":true,"page":0,"done":true,"rows":[{"command_id":"power-1","pc_id":"PC-02","action":"REBOOT_PC","power_control_mode":"simulate","requested_at":"2026-11-19T07:35:00Z","outcome":"result_received","result_code":"POWER_SIMULATED","result_received_at":"2026-11-19T07:35:02Z","reboot_confirmed_at":null,"stages":[{"stage":"request_accepted","code":null,"occurred_at":"2026-11-19T07:35:00Z"},{"stage":"partial_result","code":"POWER_ACCEPTED","occurred_at":"2026-11-19T07:35:01Z"},{"stage":"result","code":"POWER_SIMULATED","occurred_at":"2026-11-19T07:35:02Z"}]}]}
```

0번 페이지부터 `done:true`까지 순서대로 읽는다. 빈 결과도 마지막 빈 페이지를 반환한다. 페이지당 최대 100행과 프레임 크기 64 KiB를 적용한다. 요청당 2,000행을 초과하면 부분 결과 없이 `HISTORY_TOO_LARGE`를 반환하므로 기간을 좁혀 다시 조회한다. 실패는 같은 응답 식별 필드와 `ok:false`, `code`로 전달한다. 다른 코드는 `INVALID_REQUEST`, `TLS_REQUIRED`, `STORAGE_UNAVAILABLE`이다. 전환 기록 도입 전에 저장한 명령은 `stages`가 비어 있어도 기존 최종 결과 필드를 반환한다.

시험 이벤트에는 저장된 이벤트 종류별 상세 필드가 포함된다. 영상 프레임은 조회 대상에 없으며, 명령 감사 응답에는 인증정보나 내부 실행자 식별자를 포함하지 않는다. 운영 배포와 실제 GUI 공동 확인은 별도다.
