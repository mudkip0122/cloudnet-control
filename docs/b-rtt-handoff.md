# B Agent RTT — A・C 인계

2026-11-11. B Agent 구현이며 A 서버・공통 코드・C GUI는 수정하지 않았습니다. 서버 지원과 GUI 표시는 인계 후 공동 검증이 필요합니다.

## B 동작

- 같은 TCP/TLS 세션에서 RTT_PING을 보내고 probe_id가 일치하는 RTT_PONG 수신까지 단조 시계로 측정합니다. 밀리초 단위, 소수점 최대 3자리입니다. PC・서버 시각 동기화는 필요 없습니다.
- 등록 성공 응답에 **`rtt_v1: true`가 있을 때만 자동 활성화**합니다. 없으면 기존 Agent 동작을 유지하며 새 메시지나 RTT 필드를 보내지 않습니다. 재접속마다 지원 여부를 다시 확인합니다.
- 기본 즉시 1회, 이후 약 5초마다 요청하고 3초 안에 응답이 없으면 null로 처리합니다. CPU・RAM 수집, Heartbeat와 독립된 작업 스레드를 사용하고 프레임 송신 잠금을 공유합니다. 송신 잠금을 얻은 직후 타이머를 시작합니다.
- 동시 진행 요청은 한 개입니다. 지연・중복・알 수 없는 probe_id 응답은 무시하며, 잘못된 PC ID나 형식의 응답은 프로토콜 오류로 처리합니다.
- 측정 결과는 다음 METRICS에 포함합니다. RTT 전용 ACK・결과 메시지는 없습니다. 타임아웃만으로 Agent 연결을 끊지는 않습니다.
- 재접속 시 이전 값과 진행 중 요청을 초기화합니다. 성공 후 interval+timeout보다 오래된 값은 stale/null입니다. 연결이 종료된 경우 서버・GUI도 오래된 값을 숨겨야 합니다.
- `--rtt-interval 5 --rtt-timeout 3`으로 조정합니다. interval은 1~60초, timeout은 0.1~60초이면서 interval 이하입니다. `--rtt-interval 0`으로 비활성화합니다. 기존 PC 실행 파일 수정 없이 서버 지원 시 활성화됩니다.

## A에게 필요한 변경

1. RTT 요청 처리와 METRICS 필드 보관・중계를 함께 지원할 때만 Agent REGISTER_RESULT에 `rtt_v1: true`를 추가합니다.
2. 인증된 Agent의 RTT_PING만 허용하고 pc_id가 인증 세션과 같은지 검증합니다. probe_id는 비어 있지 않은 문자열 최대 128자이며 그대로 응답합니다. 요청 시각을 서버 시각으로 빼서 RTT를 계산하지 않습니다.
3. 세션의 기존 송신 잠금을 사용해 RTT_PONG을 신속히 전송합니다. 요청은 해당 Agent에게만 응답합니다. RTT_PING을 관리자에게 중계하지 않습니다.
4. METRICS의 아래 3개 필드를 검증・보관・관리자에게 전달합니다. rtt_ms는 null 또는 유한한 0 이상 숫자, rtt_status는 pending/ok/timeout/stale, rtt_timestamp는 null 또는 시간대가 포함된 ISO 8601 시각입니다. ok일 때만 rtt_ms가 숫자이며 나머지는 null입니다. 현재 요청 만료 한도(timeout)를 넘은 값은 B가 수락하지 않습니다.
5. Agent 연결 해제・서버 재시작 때 이전 RTT를 최신 값으로 표시하지 않습니다. RTT 응답 지원 전에는 rtt_v1을 광고하지 않습니다. RTT는 필수 생존 판정 대신 보조 측정입니다.

요청과 응답:

```json
{"type":"RTT_PING","pc_id":"PC-02","probe_id":"a157a8c8-e2cd-4e36-a20f-12c87a135b38"}
```

```json
{"type":"RTT_PONG","pc_id":"PC-02","probe_id":"a157a8c8-e2cd-4e36-a20f-12c87a135b38"}
```

기존 METRICS에 추가되는 필드 예시:

```json
{"rtt_ms":24.185,"rtt_status":"ok","rtt_timestamp":"2026-11-11T12:00:00Z"}
```

위 JSON은 추가 필드만 보여준 예시입니다. 실제 METRICS의 type・pc_id・timestamp・CPU/RAM/네트워크 필드는 유지합니다. rtt_timestamp는 최신 측정 성공 또는 타임아웃을 판단한 PC의 UTC 시각입니다. 측정 전은 rtt_ms=null, rtt_status=pending, rtt_timestamp=null입니다. 새 요청 대기 중에는 이전 성공 값이 유효 시간 내라면 ok로 유지되며, 그 요청이 시간 초과되면 timeout/null로 바뀝니다.

## C에게 필요한 변경

- PC별 표나 비교 화면에 **서버 RTT(ms)** 항목을 추가합니다. 예: `24.2 ms`.
- rtt_status=ok이며 유효한 숫자・시각일 때만 수치 표시. pending은 측정 대기, timeout은 응답 지연, stale・필드 미지원・연결 종료・오래된 측정은 `—`로 표시합니다. 0ms와 미측정을 구분합니다.
- METRICS 자체는 2초마다 갱신돼도 RTT는 기본 5초마다 측정됩니다. 최신 METRICS 시각만으로 RTT까지 새 측정이라고 판단하지 않습니다. rtt_timestamp와 서버의 연결・수신 상태를 함께 사용합니다. 사용자 PC 시계 오차가 있을 수 있으므로 서버 수신 시각을 활용하는 방식은 A와 합의합니다.
- 응답 타임아웃을 PC 오프라인으로 단정하지 않습니다. 끊김 상태는 서버 판정을 사용합니다. 임의의 좋음/나쁨 임계값은 이번 규격에 포함하지 않습니다.

## 해석과 검증

네트워크 이동 외에 TCP/TLS 처리・서버 응답 처리・Agent 수신 스레드 대기 시간이 포함된 **애플리케이션 RTT**입니다. ICMP ping과 같은 값이 아니며, 긴 프로세스 명령이 수신 스레드를 점유하는 동안에는 지연이나 타임아웃이 생길 수 있습니다.

`python -m unittest tests.test_rtt -v`로 단조 시계 계산, 요청 ID 대응, 시간 초과와 회복, 오래된 값, 재접속 초기화, 서버 지원 협상, 실제 루프백 소켓 왕복・METRICS 필드・작업 정리를 확인합니다. 운영 인증정보나 외부 서버는 테스트에 사용하지 않습니다. 실제 EC2 왕복과 GUI 표시는 A・C 반영 후 검증합니다.
