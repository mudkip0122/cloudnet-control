# B: Chrome 활성 창 이탈 감지 인계

## 구현 상태

B Agent만 변경했습니다. A 서버·공통 프로토콜·C GUI는 수정하지 않았습니다.
금지 프로세스 검사와 같은 주기(기본 1초, 설정 0.5~60초)로 Windows 활성 창의
프로세스를 확인하고 Chrome → 다른 프로그램 및 다른 프로그램 → Chrome 전환을 보고합니다.
별도 유예 시간은 없습니다. 샘플 사이의 짧은 전환은 놓칠 수 있습니다.

Win32 GetForegroundWindow/GetWindowThreadProcessId와 psutil을 사용합니다.
프로세스 PID·이름(최대 128자)·생성 시각만 수집하며 창 제목·경로·URL·화면·키 입력은 수집하지 않습니다.
같은 Chrome의 다른 창·탭은 구분하지 않습니다. 바탕화면·작업 표시줄 등도 다른 프로세스면 이탈입니다.
자동 차단·종료하지 않으며 부정행위 확정 판정이 아닙니다.

시작 첫 샘플은 기준점으로만 사용합니다. Chrome이 아닌 상태로 시작해도 이탈을 추측하지 않습니다.
동일 상태 반복과 Chrome 창 사이 이동은 보고하지 않습니다.
창 없음·권한 부족·조회 중 전환·비Windows는 unavailable이며 기준점을 지웁니다.
복구 후 첫 샘플도 기준점입니다. 프로세스 감지와 활성 창 감시 상태는 독립적입니다.
시험 재설정·끄기·연결 종료 후에는 이전 기준점을 이어 쓰지 않습니다.
사용자 데스크톱에 접근할 수 있는 로그인 세션에서 실행해야 합니다.

## A에게: 새 수신 지원 규격 제안

기존 EXAM_EVENT version=1 공통 필드(pc_id, exam_id, event_id, timestamp)를 유지하며
다음 kind를 추가합니다. A의 기존 허용 목록은 이를 지원하지 않으므로 먼저 검증·저장·중계를 추가해야 합니다.

```json
{"type":"EXAM_EVENT","version":1,"pc_id":"PC-02","exam_id":"exam-1","event_id":"UUID","timestamp":"2026-11-12T08:00:00Z","kind":"EXAM_WINDOW_CHANGED","transition":"left","previous_process":{"pid":100,"name":"chrome.exe","create_time":1794470000.0},"process":{"pid":200,"name":"notepad.exe","create_time":1794470001.0}}
```

- transition: left 또는 returned. returned는 Chrome 복귀이며 두 process 구조는 같습니다.
- kind=FOREGROUND_MONITOR_STATUS: status가 ok 또는 unavailable. 상태 변경 때만 전송합니다.
- 이벤트 검증·저장·GUI 중계가 준비되면 REGISTER_RESULT에 `exam_foreground_v1: true`를 추가합니다.
- B는 이 필드가 JSON true일 때만 해당 연결의 시험모드에서 활성 창 감지를 가동합니다.
  생략/false이면 기존 금지 프로세스 감지만 유지합니다. 기존 exam_events_v1/exam_control_v1 조건도 유지합니다.
- 재등록마다 기능 지원을 다시 계산합니다. 시험모드 밖에서는 활성 창을 읽지 않습니다.
- 실제 지원 구현 전에는 플래그만 켜지 않습니다. 이번 작업에서 서버 플래그·운영 배포는 변경하지 않았습니다.

## C에게

- EXAM_WINDOW_CHANGED의 left는 Chrome 이탈, returned는 Chrome 복귀로 표시합니다.
- PC·시험 ID·발생 시각·대상 프로세스 이름을 경고/이력에 표시하고 event_id 중복을 억제합니다.
- FOREGROUND_MONITOR_STATUS unavailable은 감시 공백이며 정상 응시의 증거가 아닙니다.
- 공백·재접속·모드 초기화를 가로질러 이탈 시간을 계산하지 않습니다.
- 현재 B 이벤트에는 이탈 지속 시간이 없으며 첫 관찰·공백 복구 시 이탈을 소급 확정하지 않습니다.
- 금지 프로세스 경고와 활성 창 이탈을 구분하고 부정행위 확정으로 표시하지 않습니다.

## 검증

`python -m unittest tests.test_foreground tests.test_exam tests.test_exam_commands -q`

API 모의 호출·이탈/복귀·중복 억제·감시 공백·지원 협상·시험 수명 주기를 검증합니다.
실제 사용자가 Chrome과 다른 창을 전환하는 Windows 데스크톱 및 A→C 공동 검증은 별도로 필요합니다.

API 참고: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow
