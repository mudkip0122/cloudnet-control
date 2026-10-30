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
- Agent는 2초마다 수집·전송하고 GUI는 1초마다 PC 목록과 최신 측정값을 조회합니다.
- 첫 네트워크 측정값은 `—`이며 다음 측정부터 속도가 표시됩니다.
- Agent 연결이 종료되면 오프라인으로 표시하고 현재 측정값을 숨깁니다. 측정 시각이 6초보다 오래되어도 값을 숨기고 데이터 지연을 표시합니다.
- Agent는 네트워크 단절 후 3초 간격으로 재접속합니다. 중복 PC ID 등 등록 거절 시에는 종료하며 설정을 확인해야 합니다.
- 서버가 종료되면 GUI는 기존 행을 지우고 연결 끊김을 표시하며 3초 간격으로 재접속합니다.
- GUI 창의 X로 GUI를 닫습니다. 서버·Agent는 각 터미널에서 Ctrl+C로 종료합니다.
- `admin_gui.sample`은 기존 오프라인 예시 화면이고 실제 연동 화면은 `admin_gui.main`입니다.

현재는 tkinter 표 화면입니다. 그래프·이력·원격 제어·TLS·사용자 인증은 아직 없습니다. 서버의 역할 구분은 메시지 처리 구분이며 인증을 대신하지 않습니다. 서버는 루프백에만 바인딩합니다.

검증:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_live
```

통합 검증은 별도 포트 15002로 서버와 실제 수집 Agent를 실행하고, GUI 위젯에 실제 숫자가 반영되는지와 Agent 종료 후 오프라인 전환을 확인합니다.
