# B 담당 — 프로세스 조회·보호 종료

2026-11-09 구현. B Agent의 로컬 기능과 TLS 명령 처리 기능입니다. 현재 저장소에는 C의 프로세스 목록·확인창과 TLS 관리자 명령 중계도 연동했습니다. 사용 방법·검증 범위는 [관리자 프로세스 안내](gui-process-control.md)를 참고하세요. 배포 EC2 반영은 별도입니다. 프로그램 시작, PC 종료·재부팅은 이번 변경에 포함하지 않습니다.

## 조회와 종료 정책

Windows에서 열거 가능한 프로세스를 PID 순으로 반환합니다. 각 행은 `pid`, `name`, `username`, `create_time`, `memory_bytes`, `status`, `can_stop`, `protected_reason`을 포함합니다. 권한 부족·이미 종료된 프로세스는 확인 불가능한 필드가 null이며 종료가 차단됩니다. 명령줄·환경변수·토큰은 수집하지 않습니다.

다음 조건 중 하나라도 해당하면 종료하지 않습니다.

- 시스템 PID(0~4), Agent 자신, Agent를 실행한 부모·조상 프로세스.
- 기본 보호 이름: System, Registry, smss.exe, csrss.exe, wininit.exe, winlogon.exe, services.exe, lsass.exe, svchost.exe, explorer.exe, dwm.exe 등. 전체 목록은 `agent/processes.py`의 `PROTECTED_NAMES`입니다.
- Agent와 소유 사용자가 다르거나 소유자를 확인할 수 없는 경우.
- 실행 파일이 Windows 폴더 아래에 있거나 실행 파일 경로를 확인할 수 없는 경우.
- Windows `IsProcessCritical` 판정이 참이거나 판정 API의 접근이 거부된 경우.
- 생성 시각을 확인할 수 없거나 요청의 PID·생성 시각이 현재 프로세스와 다른 경우.
- `--protect-process`로 추가 보호한 프로그램 이름에 해당하는 경우. 대소문자를 구분하지 않습니다.

조회 결과의 `can_stop`은 그 시점의 정책 판정입니다. 실제 종료 시 Agent가 정책을 다시 검사하므로 GUI에서 값을 바꿔도 보호가 해제되지 않습니다. 종료는 단일 PID에만 적용하며 자식 프로세스를 재귀 종료하지 않습니다. PID 재사용은 생성 시각 비교와 psutil의 종료 전 식별 확인으로 방어합니다.

기본 보호는 보수적인 정책입니다. 정상적인 Windows 기본 앱도 Windows 폴더에 있으면 차단됩니다. 이 목록이 모든 업무용·보안 프로그램을 자동 식별하지는 않으므로 반드시 유지해야 하는 앱은 추가 보호 이름에 등록합니다. 사용자 앱 종료 시 저장하지 않은 내용이 사라질 수 있습니다. Windows의 `psutil.terminate()`는 창의 정상 닫기 요청이 아닌 TerminateProcess 기반 종료입니다.

근거: [psutil 공식 문서](https://psutil.readthedocs.io/stable/), [Microsoft IsProcessCritical](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-isprocesscritical).

## 지금 로컬에서 사용

서버 없이 목록 조회:

```powershell
.\.venv\Scripts\python.exe -m agent.processes list | ConvertFrom-Json | Format-Table pid,name,memory_bytes,can_stop,protected_reason
```

전체 정보와 생성 시각은 표 변환 없이 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m agent.processes list
```

종료는 조회에서 선택한 PID와 `create_time`을 그대로 전달합니다. 아래 값은 형식 예시입니다. 재조회한 실제 대상 값으로 바꿉니다.

```powershell
.\.venv\Scripts\python.exe -m agent.processes stop --pid 12345 --create-time 1794190000.123456
```

추가 보호 이름을 로컬 명령에 반영하는 예:

```powershell
.\.venv\Scripts\python.exe -m agent.processes list --protect-process MyImportantApp.exe
.\.venv\Scripts\python.exe -m agent.processes stop --pid 12345 --create-time 1794190000.123456 --protect-process MyImportantApp.exe
```

로컬 CLI의 추가 보호 옵션은 해당 실행에 적용됩니다. 지속 실행 Agent에는 아래 옵션을 넣어 계속 적용합니다. 기본 보호는 옵션 없이 항상 적용됩니다.

## TLS 원격 명령 활성화

기본 Agent는 프로세스 제어 명령에 `PROCESS_CONTROL_DISABLED`로 응답합니다. TLS·PC별 토큰·`--live`를 갖춘 경우 아래 옵션으로 활성화합니다. 평문 로컬 서버에서는 활성화할 수 없습니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --host SERVER_HOST --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --pc-id PC-02 --live --enable-process-control --protect-process MyImportantApp.exe
```

추가 보호 앱이 여러 개면 `--protect-process`를 반복합니다. 권한 오류를 피하려고 Agent를 SYSTEM 계정으로 실행하지 않습니다. 현재 정책은 Agent와 같은 사용자 소유의 앱만 종료하도록 설계했습니다.

## A에게 전달할 메시지 계약

서버는 관리자 인증·대상 권한을 검증한 뒤 인증된 Agent TLS 세션으로 COMMAND를 중계합니다. Agent 자체는 관리자 GUI 로그인 상태를 알 수 없으므로 이 검증은 서버 책임입니다. TLS 서버 인증만으로 GUI 사용자 권한 검증이 완성되지는 않습니다.

목록 요청:

```json
{"type":"COMMAND","command_id":"unique-list-id","target_id":"PC-02","action":"LIST_PROCESSES","expires_at":"2026-11-09T06:00:30Z"}
```

위 시각은 예시이며 매 요청 시 현재 UTC 이후 60초 이내의 만료 시각을 생성해야 합니다. command_id는 1~128자 고유 문자열입니다. 나이브 시각·만료·60초 초과·다른 target_id는 거절됩니다.

목록 응답은 최대 40행씩 분할합니다. 모든 페이지는 한 번의 요청을 처리한 목록에서 생성되며 `page`는 0부터 시작합니다. 빈 목록도 한 페이지입니다. C는 command_id별로 `pages`개를 모아서 화면을 갱신해야 합니다. 다른 응답·METRICS·HEARTBEAT가 섞여 올 수 있습니다.

```json
{"type":"COMMAND_RESULT","pc_id":"PC-02","command_id":"unique-list-id","ok":true,"code":"PROCESS_LIST","page":0,"pages":1,"processes":[{"pid":12345,"name":"demo.exe","username":"PC\\user","create_time":1794190000.123456,"memory_bytes":4096,"status":"running","can_stop":true,"protected_reason":null}]}
```

종료 요청은 최신 목록의 PID·create_time을 모두 전달합니다.

```json
{"type":"COMMAND","command_id":"unique-stop-id","target_id":"PC-02","action":"STOP_PROCESS","pid":12345,"create_time":1794190000.123456,"expires_at":"2026-11-09T06:00:30Z"}
```

응답 코드는 다음과 같습니다.

| code | 의미 |
|---|---|
| TERMINATED | 종료 요청 후 해당 프로세스 종료 확인 |
| TERMINATION_REQUESTED | 요청했으나 2초 내 종료 확인 못함. 재조회 필요 |
| PROTECTED | 정책상 종료 금지. reason으로 차단 이유 전달 |
| PID_REUSED | 생성 시각 불일치. 목록 새로고침 필요 |
| NOT_FOUND | 이미 종료됨 |
| ACCESS_DENIED / PROCESS_ERROR | 권한 거절 / OS 처리 오류 |
| INVALID_IDENTITY | 잘못된 PID·생성 시각 |
| DUPLICATE_COMMAND | 동일 명령 ID 재처리 거절 |
| PROCESS_CONTROL_DISABLED | 해당 Agent에서 원격 프로세스 제어 비활성 |

중복 명령 ID는 60초간 보관하며 동일 Agent 프로세스의 재접속에도 유지됩니다. 동시에 최대 4096개를 보관하고 포화 시 COMMAND_CAPACITY로 거절합니다. Agent 프로그램 재시작 후에는 이 메모리 기록이 초기화되므로 서버도 명령 ID와 처리 이력을 관리하고 재전송을 막아야 합니다. 종료 로그에는 PID와 결과 코드만 기록합니다. 실행자·명령 ID·대상·시각 감사 기록은 A 서버가 담당합니다.

## C에게 전달할 화면 동작

- 목록에 PID·이름·메모리·사용자·종료 가능 여부·보호 이유 표시.
- can_stop=false 행은 종료 버튼 비활성화.
- 종료 전 PC·이름·PID 확인창 표시. 사용자가 확인하면 최신 목록의 create_time도 함께 전달.
- 결과 코드를 확인한 뒤 목록 새로고침. TERMINATION_REQUESTED는 종료 완료로 표시하지 않음.
- 응답 페이지 일부만 도착하거나 연결이 끊기면 불완전한 목록임을 표시하고 종료 동작을 막음.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_process_control
.\.venv\Scripts\python.exe -m tests.smoke_tls
```

smoke_process_control은 임시 CA·토큰·로컬 TLS 서버·실제 Agent를 사용합니다. 자체 생성한 대기용 Python 프로세스만 종료하며, 사용자 앱이나 시스템 프로세스를 테스트 종료하지 않습니다. 목록 분할 수신, Agent 보호 표시, 생성 시각 불일치 거절, 실제 종료 확인, 중복 명령 거절을 검증합니다.
