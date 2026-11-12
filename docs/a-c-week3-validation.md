# A·C 3주차 공동 검증 준비

작성일: 2026-11-12. 작업 브랜치: `integration/a-exam-batch-control`.

## 버전과 담당 경계

- A 서버 구현: `c5ecfef` (subscribe_exam 중계·exam_mode 반영·시험 지원 플래그·RTT 서버 신선도).
- C GUI 구현/합의: `35004c3`, `c/week3-dashboard`.
- C 브랜치에 포함된 B: `57b530b`. 원 연동 규격은 `3a091ef`.
- A 브랜치에 C를 충돌 없이 병합했습니다. A가 B Agent/C GUI 파일을 직접 편집한 내용은 없습니다. 인증서·토큰·개인키도 변경하지 않았습니다.

C 합의 문서의 A `ef3f64e` 미구현 안내는 이전 버전 기준입니다. A 후속 `c5ecfef`에는 요청한 서버 기능이 구현되어 있습니다. 실제 형식은 [A→C 규격](a-c-exam-contract.md)을 사용합니다.

## 호환 확인

1. 세 새 action은 B 요청과 COMMAND_RESULT를 유지합니다. C는 forbidden_processes를 명시적으로 보내므로 B 최신의 기본 목록 생략 기능을 A가 아직 허용하지 않아도 호환됩니다. A에서 생략 정책을 임의로 변경하지 않았습니다.
2. C의 별도 구독 연결은 subscribe_exam:true로 등록합니다. A는 검증·저장한 원문 EXAM_EVENT를 보내며 중복 event_id를 다시 중계하지 않습니다. C의 수신 큐/이력/중복 억제는 C 구현을 그대로 사용합니다.
3. C 현재 버전은 구독 등록에 subscribe_status:true를 넣지 않습니다. 따라서 다른 관리자의 모드 변경·연결 변화는 현재 C의 1초 LIST_DEVICES 폴링에서 전달됩니다. 즉시 STATUS_UPDATE push까지 원하면 C 담당자가 그 구독 플래그를 추가할 수 있습니다. 현재 폴링 동기화도 정상 지원하며 필수 수정은 아닙니다.
4. C는 metrics_stale·last_metrics_received를 사용하고 Agent 시각 범위를 보수적으로 검사합니다. A가 추가로 제공하는 rtt_stale·rtt_age_seconds를 C가 아직 사용하지 않아도 기존 보수적 표시와 호환됩니다. 시계 오차가 크면 C에서 수치가 숨겨질 수 있습니다.
5. 등록·단절 시 exam_mode:null, 새 agent_session_id를 제공합니다. 새 연결의 상태는 GET_EXAM_STATUS로 확인하며, 감지 이력을 현재 모드/현재 실행 상태로 사용하지 않습니다.

## 로컬 통합 검증

`tests.test_server_exam_stream`에 실제 C ExamEventClient와 ExamStore를 A TLS 서버에 연결한 검증을 추가했습니다. 접속 대상만 실제 로컬 TLS 연결로 연결하고 C의 등록·파싱·큐·폴링·상태/이력 처리 코드는 수정 없이 실행합니다.

확인 범위:

- C 구독 등록·원문 경고 수신·이력 반영·중복 event_id 한 번 표시.
- 다른 관리자 명령의 모드 켜짐/꺼짐을 C 폴링 스냅샷에 반영.
- RTT METRICS 중계와 C의 ms 표시.
- Agent 단절·동일 ID 재접속 후 모드 미확인, 세션 ID 변경, 감지 이력 보존.
- 기존 A TLS 테스트의 B 실제 stream 켜기→감지→끄기→조회·RTT.

병합 직후 전체 테스트에서는 이 PC의 가상환경에 Matplotlib이 없어 일부 모듈을 불러오지 못했습니다. C의 requirements.txt로 로컬 의존성을 설치했으며 팀원 소스 수정으로 우회하지 않았습니다.

최종 결과: B 57b530b·C 35004c3와 A 변경을 포함한 **전체 181개 통과, skip 없음**. 실행 명령은 `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`이며 C의 Tk 화면 검증을 위해 샌드박스 밖에서 실행했습니다. 로컬 TLS 검증이며 실제 EC2/3대 PC 공동 시연을 대신하지 않습니다.

## 공동 검증에 전달할 내용

> A 구현은 c5ecfef부터 준비되어 있고 C 35004c3를 integration/a-exam-batch-control에 병합했습니다. docs/a-c-exam-contract.md와 이 검증 기록을 확인해 주세요. 세 명령은 B 규격 그대로이며 구독·모드 스냅샷·시험 지원 플래그를 지원합니다. 현재 C의 별도 구독과 1초 폴링 상태 동기화로 연결할 수 있습니다. 운영 배포 및 실제 PC 3대 검증은 아직 별도입니다.

남은 공동 검증은 운영 배포 일정·서버 DB 쓰기 경로 준비, 각 PC 소유자의 최신 Agent 재실행, 3대 동시 시험 켜기/감지/끄기·일괄 종료·RTT·단절 복구입니다. 자체 생성하고 소유자가 승인한 테스트 프로세스만 종료합니다. 실제 검증 기록·리뷰 전 main에 병합하지 않습니다.
