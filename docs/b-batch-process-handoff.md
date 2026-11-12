# B 일괄 프로세스 종료 — C 화면 및 A 중계 인계

2026-11-11. B 구현 완료. 수정 범위는 agent/processes.py, agent/process_commands.py와 B 테스트·문서입니다. A 서버·공통 코드·C GUI는 수정하지 않았습니다. **실제 GUI 사용은 A·C 연동 후 가능합니다.**

## 동작

- `STOP_PROCESSES` 명령으로 한 PC의 명시적인 PID·생성 시각 목록(1~100개)을 받습니다. GUI가 같은 이름을 묶든 여러 행을 선택하든 동일한 명령을 사용합니다.
- 이름으로 검색해서 새 대상을 추가하지 않습니다. 확인창에 나온 목록만 종료하며 자식 프로세스를 재귀 종료하지 않습니다. 사용자가 확인한 뒤 새로 실행된 같은 이름의 프로그램은 포함되지 않습니다.
- 전체 입력을 먼저 검증합니다. 잘못된 PID·생성 시각, 같은 PID 중복, 빈 목록, 100개 초과는 INVALID_TARGETS로 전체 거절하고 아무것도 종료하지 않습니다.
- 각 항목마다 기존 단일 종료와 같은 live PID·생성 시각 및 보호 정책을 재검증합니다. 보호됨·이미 종료됨·PID 재사용·권한 부족은 항목별로 반환하고 다음 대상으로 진행합니다.
- 기존 `--enable-process-control`과 TLS·토큰·live 조건이 그대로 필요합니다. 명령 ID·대상·만료·중복 방지도 기존 처리와 같습니다.
- 순차 처리합니다. 각 종료 확인은 최대 0.1초이며 미확인은 TERMINATION_REQUESTED입니다. 처리 시작 후 5초 예산을 넘거나 명령 만료 시 아직 시작하지 않은 항목은 건너뜁니다. OS 조회 호출 자체의 지연까지 강제로 중단하는 제한은 아닙니다.
- 일부 성공을 되돌리지 않습니다. 자동 재시도하지 말고 결과 확인 후 목록을 다시 조회합니다. 종료 로그는 PID·결과 코드만 기록합니다.

## C에게 전달할 구현 사항

1. **그룹·펼치기:** 이름의 casefold 값이 같은 행을 묶고, 상위 행에 이름·개수·합산 메모리를 표시합니다. 하위 행에는 기존 PID·사용자·메모리·생성 시각·보호 사유를 표시합니다. 이름이 null인 항목을 하나의 종료 그룹으로 묶지 않습니다. 같은 이름이라도 다른 경로·사용자의 프로그램일 수 있으므로 실행 인스턴스가 같다는 뜻으로 표시하지 않습니다.
2. **선택:** 그룹 종료는 완전히 수신한 최신 목록의 `can_stop=true`이고 PID·생성 시각이 유효한 행만 대상으로 만듭니다. 보호 항목 수는 별도로 표시합니다. 대상이 없으면 버튼을 비활성화합니다. 같은 PID는 한 번만 포함합니다.
3. **확인창:** 대상 PC·그룹 이름·실제 종료할 PID 목록과 수·제외한 보호 항목 수를 표시합니다. “강제 종료 시 미저장 내용이 사라질 수 있으며 브라우저의 시험 창도 닫힐 수 있음”을 안내합니다. 사용자가 확인한 목록을 고정해 전송하며 이름만 전송하지 않습니다.
4. **100개 제한:** 100개가 넘으면 범위를 줄여 다시 선택하도록 안내합니다. 이번 동작에서는 자동으로 여러 배치로 나누어 추가 종료하지 않습니다.
5. **결과:** 아래 단일 응답의 results를 각 행과 매칭해 표시합니다. `ok=true`는 요청 처리 완료이며 전체 종료 성공이 아닙니다. 전체 종료 확인은 `all_terminated=true` 또는 모든 항목 code가 TERMINATED인 경우입니다. counts로 요약하고 개별 실패·보호 사유도 제공합니다.
6. **갱신:** 결과 후 목록을 재조회합니다. TERMINATION_REQUESTED는 종료 완료가 아니며, BATCH_TIME_LIMIT·EXPIRED_DURING_BATCH는 해당 항목을 종료하지 않고 건너뛴 결과입니다. 연결 단절·타임아웃은 결과 미확인으로 표시하고 새로 조회합니다. 같은 요청을 자동 재전송하지 않습니다.
7. **통신:** 기존 관리자 인증 연결에서 A 서버로 COMMAND를 전송하고 요청 command_id·대상 PC를 검증합니다. 새 action은 분할 응답이 아니라 **한 개의 COMMAND_RESULT**입니다. 요청 중 버튼을 비활성화하고 METRICS·Heartbeat·다른 응답과 구분합니다.

## 요청·응답 규격

매번 새 command_id와 현재 시각 기준 미래 60초 이내의 expires_at을 생성합니다. 아래 값은 형식 예시이며 실제 조회한 ID·생성 시각으로 교체해야 합니다.

```json
{"type":"COMMAND","command_id":"unique-batch-id","target_id":"PC-02","action":"STOP_PROCESSES","expires_at":"2026-11-11T06:00:30Z","targets":[{"pid":12345,"create_time":1794190000.123456},{"pid":12346,"create_time":1794190001.123456}]}
```

결과는 요청 순서대로 최대 100행이며 별도 페이지가 없습니다.

```json
{"type":"COMMAND_RESULT","pc_id":"PC-02","command_id":"unique-batch-id","ok":true,"code":"PROCESS_BATCH_RESULT","all_terminated":false,"counts":{"TERMINATED":1,"PROTECTED":1},"results":[{"pid":12345,"create_time":1794190000.123456,"ok":true,"code":"TERMINATED"},{"pid":12346,"create_time":1794190001.123456,"ok":false,"code":"PROTECTED","reason":"PROTECTED_NAME"}]}
```

항목별 code는 기존 TERMINATED, TERMINATION_REQUESTED, PROTECTED(reason 포함), PID_REUSED, NOT_FOUND, ACCESS_DENIED, PROCESS_ERROR 및 새 EXPIRED_DURING_BATCH, BATCH_TIME_LIMIT입니다. 입력 전체 거절은 `ok:false, code:INVALID_TARGETS`이며 results는 없습니다. PROCESS_CONTROL_DISABLED·WRONG_TARGET·INVALID_EXPIRY·EXPIRED_OR_TOO_FAR·DUPLICATE_COMMAND·COMMAND_CAPACITY 등 기존 명령 거절도 유지합니다.

## A에게 필요한 최소 변경

- 현재 서버 action 허용 목록은 LIST_PROCESSES·STOP_PROCESS만 받습니다. **STOP_PROCESSES 추가가 필수**이며 반영 전에는 UNSUPPORTED_ACTION입니다.
- 관리자 권한·TLS·대상·만료를 기존처럼 검증하고 targets가 유효한 명시적 배열인지 검증한 뒤 Agent로 전달합니다. 이름만 지정한 종료·임의 셸 명령은 허용하지 않습니다.
- 단일 COMMAND_RESULT의 PROCESS_BATCH_RESULT와 개별 결과를 원 요청 관리자에게 중계합니다. 프로세스 조회 페이지 조합 규칙을 이 action에 적용하지 않습니다.
- 감사 기록에 명령 ID·실행자·대상 PC·확인한 PID/생성 시각 목록·각 결과를 남기고 재전송을 막습니다. 명령 중단·연결 해제 시 부분 실행 가능성을 고려합니다.

## 검증 범위

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_process_batch tests.test_processes tests.test_exam_commands tests.test_exam tests.test_heartbeat tests.test_agent_security -q
```

총 49개 통과. 새 일괄 처리 테스트 7개는 전체 입력 검증·중복 거절·실시간 보호 재검증·PID 재사용·권한 오류 후 계속 처리·시간 예산·중간 만료·부분 성공 요약·100개 응답 크기·Agent 소켓 응답과 측정값 유지 등을 확인합니다. 종료 호출은 테스트용 모의 프로세스를 사용했으며 운영 프로그램을 실제로 종료하지 않았습니다. A·C를 경유한 실제 일괄 종료 통합 시험은 아직 수행하지 않았습니다.
