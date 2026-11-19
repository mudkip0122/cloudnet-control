# A–C 실시간 경고 구독 제안 v1

C가 제안한 기준을 A 서버에 적용한다. CPU·RAM별로 90% 이상 샘플이 10초 연속되고 샘플 간격이 6초 이하면 발생, 85% 미만 샘플에서 복귀한다. 결측이나 6초 초과 공백은 새 발생의 지속 시간을 초기화한다. 열린 경고는 공백·연결 종료·서버 재시작을 넘어 유지하고 복귀 시 같은 `alert_id`를 사용한다. 연결 종료·재접속은 별도의 `DEVICE_OFFLINE` 발생·복귀다. 이 규격에 대한 C의 구현 확인과 운영 공동 검증은 남았다.

## 인증·구독

TLS와 기존 관리자 토큰을 사용하는 기존 관리자 구독 연결에서 `subscribe_alerts:true`를 명시한다. 상태 동기화를 위해 `subscribe_status:true`도 권장한다. 인증되지 않은 연결과 Agent는 구독할 수 없다. 지원 서버의 `REGISTER_RESULT`에는 `alert_stream_v1:true`가 있다. 공통 관리자 토큰은 모든 PC 권한을 가진다.

```json
{"type":"REGISTER","role":"admin","token":"기존 관리자 토큰","subscribe_status":true,"subscribe_alerts":true}
```

등록 응답 이후 서버는 구독 확정 시점의 열린 경고를 `ALERT_SNAPSHOT`으로 보낸다. 하나의 스냅샷은 `page` 0부터 순서대로 전달하며 마지막 페이지가 `done:true`다. 빈 목록도 빈 배열의 마지막 페이지를 보낸다. 한 페이지는 최대 20건이며 스냅샷은 최대 600건이다. 이보다 많으면 연결을 오류로 종료하며 목록을 정상 완료한 것처럼 보내지 않는다.

```json
{"type":"ALERT_SNAPSHOT","version":1,"page":0,"done":true,"alerts":[{"pc_id":"PC-02","kind":"CPU_HIGH","alert_id":"alert-1","state":"raised","observation":"unknown"}]}
```

`observation:unknown`은 CPU/RAM의 최신 측정이 없거나 null·6초 초과·오프라인이어서 현재 상태를 확인할 수 없다는 뜻이다. `known`은 서버가 해당 지표의 신선한 측정값을 가지고 있다는 뜻이다. 오프라인 경고 자체는 서버가 현재 단절을 알고 있으므로 `known`이다. 스냅샷은 현재 상태의 복원이며 신규 경고 알림으로 반복해서 울리지 않는다. C는 마지막 페이지까지 받은 뒤 기존 열린 목록을 교체한다.

## 발생·복귀 전달

DB에 먼저 저장한 뒤 해당 구독 연결에 다음 형식으로 보낸다. `kind`는 `CPU_HIGH`, `RAM_HIGH`, `DEVICE_OFFLINE`, `state`는 `raised` 또는 `resolved`다. `occurred_at`은 서버 UTC 수신 시각이다.

```json
{"type":"ALERT_EVENT","version":1,"pc_id":"PC-02","kind":"CPU_HIGH","alert_id":"alert-1","state":"resolved","occurred_at":"2026-11-19T07:00:00+00:00"}
```

초기 스냅샷 페이지를 모두 큐에 넣고 난 뒤의 전환만 같은 큐로 추가하므로 전환이 스냅샷 중간에 끼지 않는다. 중복 키는 PC ID·종류·alert_id·상태다. 느린 관리자 큐가 가득 차면 해당 연결을 종료하며 재접속과 스냅샷 복원이 필요하다. 연결이 끊긴 동안 발생하고 복귀까지 끝난 경고는 열린 목록에 없으므로 `GET_HISTORY`로 누락 구간을 조회해야 한다.

스냅샷 이후의 감시 공백은 기존 `STATUS_UPDATE`와 주기적 `LIST_DEVICES`의 `online`, `metrics_stale`, 지표별 null 값으로 판단한다. 경고가 열려 있어도 수신 공백에는 현재 상태 미확인으로 표시하고 정상 복귀를 추정하지 않는다. 연결이 복구되면 최신 측정·스냅샷·전환에 따라 표시한다. 운영 서버 적용과 C GUI를 이용한 공동 검증 여부는 별도로 확인한다.
