# 1주차 함께 시작하기

실제 측정값을 서버와 GUI까지 연결하려면 [실시간 모니터링 안내](live-monitoring.md)를 사용하세요. 아래는 개별 기능을 연습하는 순서입니다.

모든 명령은 저장소 루트에서 실행합니다. Python 3.11 이상을 사용하며, 현재 PC에서 발견한 버전은 3.13입니다. 이 단계에서는 `.env`를 읽지 않고 명령행 인자를 사용합니다.

## 공통 환경

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`python` 명령이 없다면 Python 설치 경로를 확인하고 첫 명령의 `python`을 해당 실행 파일 경로로 바꿉니다. GUI에는 Python의 Tcl/Tk 구성 요소가 필요합니다.

## A: 로컬 서버 실행

터미널 1:

```powershell
.\.venv\Scripts\python.exe -m server.main
```

`Local registration server: 127.0.0.1:5000`을 확인합니다.

## B: 등록 왕복 및 실제 값 수집

같은 PC의 터미널 2에서 예시 ID를 자신의 PC 고유값으로 바꿔 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --pc-id YOUR-PC-ID --pc-name TEAM-B-PC
.\.venv\Scripts\python.exe -m agent.collect --pc-id YOUR-PC-ID
```

첫 명령은 `REGISTER_RESULT`, `ok: True`, 같은 PC ID를 출력하고 끝납니다. 두 번째는 2초마다 실제 CPU·RAM·네트워크 값을 콘솔에 출력합니다. 수집기는 아직 서버로 전송하지 않습니다. 종료는 Ctrl+C입니다.

## C: 서버 없이 화면 검토

```powershell
.\.venv\Scripts\python.exe -m admin_gui.sample
```

현재는 기본 tkinter 표로 만든 오프라인 화면 초안입니다. 표시 필드를 검토한 뒤 CustomTkinter의 PC 목록·상세 영역으로 확장합니다. 실시간 연결·그래프·실제 제어는 아직 없습니다.

화면 필드: 목록은 PC 이름·PC ID·온라인 여부·최근 수신 시각, 상세는 CPU·RAM·업로드·다운로드·측정 시각. 수집 시각과 서버의 최근 수신 시각은 별도로 취급합니다.

## 오늘 완료할 항목

- A: 등록 왕복을 확인하고 [프로토콜](protocol.md)의 다음 흐름을 B·C와 검토합니다.
- B: 실제 PC ID와 PC 이름을 정하고, 정상 측정값 및 첫 네트워크 `null` 출력 예시를 공유합니다.
- C: 샘플 화면을 확인하고 목록·상세 화면에서 추가로 필요한 필드를 A에게 전달합니다.
- 전체: 필드·단위·결측값 처리에 동의한 뒤 공통 코드를 기준으로 개발합니다.

다른 PC와 EC2 연결은 TLS·인증 준비 후 진행합니다. 현재 루프백 서버는 각자의 PC에서 각각 실행해 연습합니다.

## 통신 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
