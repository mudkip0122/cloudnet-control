# A 서버 통합 작업 — B 3a091ef 기준

작성일: 2026-11-12. 브랜치: `integration/a-exam-batch-control`.
기반: C 통합 `7afad33` (B `3a091ef` 포함).

## 범위와 현재 상태

A는 `server/`, A 소유 테스트와 문서만 직접 수정했습니다. B Agent·C GUI·기존 인증서·개인키·토큰은 수정하지 않았습니다. B·C 소스는 팀원의 커밋을 통합 브랜치로 가져왔습니다.

확정된 B 메시지 규격으로 다음을 구현했습니다.

- 인증된 PC별 METRICS 검증·보관·조회. CPU/RAM·Mbps·null 규칙 유지, 수신 후 6초 초과 여부를 서버 단조 시계로 판정. Heartbeat와 측정값 수신 시각 분리.
- Agent의 RTT_PING에 같은 PC ID·probe_id로 RTT_PONG 응답. RTT 3개 필드를 함께 검증·보관하며 구형 Agent의 필드 생략도 허용. REGISTER_RESULT의 rtt_v1:true 및 heartbeat_interval 지원.
- TLS 관리자 명령에 SET_EXAM_MODE·GET_EXAM_STATUS·STOP_PROCESSES 추가. 시험 설정·1~100개 고정 PID/생성 시각·중복 PID·만료·역할·대상 연결 검증. 프로세스 보호 판정은 B에 유지.
- 시험/일괄 결과의 요청자·Agent 세션·명령 ID와 결과 필드 검증. 일괄 결과의 대상 순서·개별 ID·요약 대조.
- EXAM_EVENT의 버전·PC·시험 ID·종류별 필드 검사, 초당 PC별 최대 200개 제한, event_id 중복 저장 억제. 새 ACK 메시지를 보내지 않음.
- SQLite에 시험 이벤트 및 명령 접수·설정/대상·개별 결과·결과 수신 시각 저장. 명령 ID를 실행 전달 전에 영속 예약하여 서버/Agent 재시작 뒤 중복 실행을 방지. 결과 미수신은 성공으로 기록하지 않음.
- 관리자별 최대 32개 큐로 프로세스 결과 전달. 느린 GUI의 송신을 Agent 수신 스레드에서 기다리지 않으며 포화된 관리자만 연결 종료.

**2026-11-12 후속:** C의 연동 요청을 반영해 시험 구독·상태 변경·서버 시각 필드를 구현했습니다. 확정 지원 형식은 [A→C 연동 규격 v1](a-c-exam-contract.md)을 사용합니다. TLS·인증·감사 저장소를 갖춘 서버는 이제 `exam_events_v1:true`, `exam_control_v1:true`를 광고합니다. C 화면 연결과 실제 PC 3대 공동 검증은 남아 있습니다. main 병합·EC2 배포는 진행하지 않았습니다.

## C 요청에 따른 A 지원 형식

아래 형식을 A 통합 브랜치에 구현했습니다. B의 메시지 필드는 변경하지 않습니다.

1. 기존 LIST_DEVICES 폴링 유지. 별도 TLS 관리자 연결의 REGISTER에 subscribe_exam:true를 넣으면 원문 EXAM_EVENT를 수신. 기존 미구독 연결에는 이벤트를 보내지 않음.
2. subscribe_status:true로 모드 결과·감시 상태·Agent 연결 변화의 STATUS_UPDATE 수신. DEVICE_LIST에도 exam_mode·exam_state_received_at·agent_session_id 포함. 새 연결/단절은 exam_mode:null로 무효화.
3. METRICS의 RTT 원문 필드 유지. last_metrics_received·metrics_age_seconds·metrics_stale, last_rtt_received·rtt_age_seconds·rtt_stale 제공. 같은 RTT의 반복 전송으로 RTT 수신 시각을 갱신하지 않음. GUI/Agent 시계 차이 대신 서버 단조 시계 기반 경과 시간 사용.
4. 현재 공통 관리자 토큰으로 인증된 감독자는 같은 전체 PC 범위를 열람. 개인별 신원·시험별 권한 구분은 지원하지 않음.

C 담당자가 규격 문서에 따라 구독/경고·이력·상태·신선도 표시를 연결한 뒤 공동 검증합니다. A는 C 소스를 수정하지 않습니다.

## DB 운영

로컬 서버는 기본 `runtime/server-audit.sqlite3`를 사용하며 `--audit-db`로 지정합니다. 소스 관리에서 제외되어 있습니다. 저장소는 서버만 접근하며 GUI에 DB 파일을 공유하지 않습니다. 이벤트·명령은 기본 30일 보관하고 시작 및 새 기록 처리 때 정리합니다. 실행자는 관리자 접속별 임의 ID로 기록하며, 공통 관리자 토큰으로 개인의 신원을 확인했다고 표현하지 않습니다.

DB를 열 수 없으면 서버 시작을 중단합니다. 접수/저장 중 DB 오류가 나면 해당 세션을 닫고 명령을 자동 재전송하지 않습니다. 접수 기록만 있는 명령은 결과 미확인입니다. DB 보존이 재시작 후 중복 방지의 조건입니다. DB 삭제/교체 시 기록 보장이 사라집니다.

기존 EC2 systemd의 ProtectHome=read-only 설정에서는 홈 아래 DB 쓰기가 차단됩니다. 배포 시 A가 전용 쓰기 경로(예: /var/lib/cloudnet-control)를 서비스 사용자 소유·권한 0700으로 준비하고 `--audit-db`를 그 경로로 지정해야 합니다. 기존 인증정보는 그대로 사용합니다. 이번 작업에서 서비스 설정은 변경하지 않았습니다.

## B에게 전달할 내용

> B `3a091ef` 규격 변경 없이 A 명령 중계·시험 이벤트 저장/구독·RTT를 구현했고, TLS·인증·감사 저장소가 준비된 통합 서버에서 시험 지원 플래그를 켰습니다. 운영 EC2는 아직 배포 전입니다. 배포 안내 후 PC-01/02/03에서 기존 ID·인증서·각 PC 토큰을 유지하고 최신 실행 파일로 재실행해 주세요. C와 실제 3대 공동 검증이 남아 있습니다.

## C에게 전달할 내용

> A 작업 브랜치는 integration/a-exam-batch-control입니다. 세 명령은 B 문서 그대로 COMMAND/COMMAND_RESULT를 사용합니다. 별도 TLS 관리자 연결에 subscribe_exam:true와 subscribe_status:true를 설정해 원문 이벤트 및 상태 변경을 받고, 그 연결에서도 5초마다 LIST_DEVICES를 보내 주세요. 구체적인 PC 상태·RTT 서버 시각 필드는 docs/a-c-exam-contract.md에 정리했습니다. C에서 이벤트 보존 큐·경고/이력·event_id 중복 억제·감시 상태·신선도 표시를 연결해 주세요. main 병합은 통합 검증 후 진행합니다.

## 검증 및 남은 작업

검증 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

새 A 테스트는 잘못된 설정·RTT 조합·위장 PC·명령 결과의 대상 불일치, 이벤트 중복·빈도 제한, DB 재연결 뒤 중복 저장/실행 차단, 느린 관리자 격리, Heartbeat와 측정 지연 분리를 확인합니다. 실제 사용자 앱 종료는 수행하지 않습니다.

2026-11-12 검증: B·C 커밋을 포함한 전체 **154개 통과, skip 없음**. 샌드박스 안에서는 Tcl 초기화 제한으로 C 화면 테스트 2개가 실패했으며, 같은 명령을 샌드박스 밖에서 실행해 전부 통과했습니다. C 코드 수정은 없었습니다. 실제 EC2·3대 PC 시험 연동 결과와 구분합니다.

후속 A TLS 검증에는 B의 수정 없는 stream 코드를 사용하는 시험 켜기→감지→끄기→조회·RTT, 원문 이벤트 구독/중복 억제, 다른 관리자 상태 갱신, 요청자 종료 후 결과 감사·알림, Agent 재접속 무효화, 같은 RTT 반복 수신 시 만료 유지가 포함됩니다.

후속 최종 검증: **전체 162개 통과, skip 없음**. C의 기존 관리자 연결·화면 테스트도 수정 없이 통과했습니다. 기존 DEVICE_LIST 최상위 구조를 유지하고 새 서버 시각 필드는 각 PC 객체에 제공합니다.

남은 작업: C 구독/화면 연결 → 전체 통합 검증 → 쓰기 가능한 DB 경로로 EC2 배포 → 각 PC 소유자가 승인한 테스트 프로세스로 실제 3대 시험/일괄/RTT 공동 검증 → 결과 기록·리뷰 → main 병합. 실제 3대 시연이나 시험 전체 완료로 체크하지 않습니다.
