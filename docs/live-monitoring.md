# 로컬 실시간 모니터링

저장소 루트에서 가상환경과 의존성을 준비한 뒤 터미널 세 개로 실행합니다. 기존 등록 연습 서버가 실행 중이면 Ctrl+C로 종료하고 새 서버를 시작합니다.

```powershell
# 터미널 1: 서버
.\.venv\Scripts\python.exe -m server.main

# 터미널 2: 실제 값 전송 (ID는 자신의 PC 고유값으로 지정)
.\.venv\Scripts\python.exe -m agent.main --pc-id YOUR-PC-ID --live

# 터미널 3: 실제 값 GUI
.\.venv\Scripts\python.exe -m admin_gui.main
```

- 현재는 세 프로그램을 같은 PC에서 실행합니다. 주소는 `127.0.0.1`, 기본 포트는 `5000`입니다. 다른 포트는 세 명령에 동일한 `--port`를 추가합니다.
- Agent는 `--host`로 서버 IP 또는 호스트 이름을 지정할 수 있습니다. 생략 시 `127.0.0.1`이며 등록 전용 모드와 `--live` 모두 지원합니다. 현재 서버·GUI는 로컬 전용입니다. EC2 실접속은 A가 TLS·인증을 준비한 후 규격에 맞춰 연결 코드를 추가합니다.
- Agent는 2초마다 수집·전송하고 GUI는 1초마다 PC 목록과 최신 측정값을 조회합니다.
- 첫 네트워크 측정값은 `—`이며 다음 측정부터 속도가 표시됩니다.
- Agent 연결이 종료되면 오프라인으로 표시하고 현재 측정값을 숨깁니다. 측정 시각이 6초보다 오래되어도 값을 숨기고 데이터 지연을 표시합니다.
- Agent는 네트워크 단절 후 3초부터 최대 30초까지 간격을 늘려 재접속합니다. 중복 PC ID 등 등록 거절 시에는 종료하며 설정을 확인해야 합니다.
- 서버가 종료되면 GUI는 기존 행을 지우고 연결 끊김을 표시하며 3초 간격으로 재접속합니다.
- GUI 창의 X로 GUI를 닫습니다. 서버·Agent는 각 터미널에서 Ctrl+C로 종료합니다.
- `admin_gui.sample`은 기존 오프라인 예시 화면이고 실제 연동 화면은 `admin_gui.main`입니다.

현재는 tkinter 표 화면입니다. 그래프·이력·원격 제어·TLS·사용자 인증은 아직 없습니다. 서버의 역할 구분은 메시지 처리 구분이며 인증을 대신하지 않습니다. 서버는 루프백에만 바인딩합니다.

검증:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_live
.\.venv\Scripts\python.exe -m tests.smoke_reconnect
```

통합 검증은 별도 포트 15002로 서버와 실제 수집 Agent를 실행하고, GUI 위젯에 실제 숫자가 반영되는지와 Agent 종료 후 오프라인 전환을 확인합니다.

재접속 검증은 임시 로컬 포트와 `--host localhost`로 실제 서버·Agent를 실행하고 서버를 종료·재시작합니다. Agent 프로세스를 유지한 채 동일 PC ID로 재등록하고 새로운 측정값을 보내는지 확인합니다.

## B 담당 실행 예시

```powershell
# 같은 PC의 서버로 실제 데이터 전송
.\.venv\Scripts\python.exe -m agent.main --host localhost --port 5000 --pc-id YOUR-PC-ID --live
```

PC ID를 명시하면 재실행할 때도 동일하게 지정합니다. `--pc-id`를 생략하면 `runtime/agent-id.json`에 자동 생성·저장한 ID를 재사용합니다. 자세한 설치·로그·인계 안내는 [B 담당 문서](agent-windows.md)를 확인하세요. A에게 받을 항목은 서버 주소·포트, TLS 인증서 및 서버 이름 검증 방법, Agent 인증 규격입니다. 이 설정을 받은 후 EC2 연결을 진행합니다.
