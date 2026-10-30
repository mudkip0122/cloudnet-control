# B 담당 — Windows Agent 실행 및 인계

## 설치

PowerShell에서 저장소 루트로 이동하고 Python 3.11 이상으로 준비합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

가상환경을 활성화하지 않고 실행 파일을 직접 사용하므로 Activate.ps1 실행 정책을 바꿀 필요가 없습니다. `.env` 자동 로딩은 구현하지 않았습니다.

## 로컬 실행

서버를 별도 터미널에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m server.main
```

Agent를 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --live
```

처음 실행하면 `runtime/agent-id.json`에 UUID를 저장합니다. 다음 실행과 재접속에서도 같은 ID를 읽습니다. 이 파일은 Git에서 제외되며 다른 PC에 복사하지 않습니다. 저장소 전체를 다른 PC로 복사할 때는 `runtime/`을 제외합니다. 새 경로에 새로 설치하면 새 ID가 생성되므로 설치를 옮길 때 ID를 유지하려면 기존 ID 파일을 해당 PC에서만 이전합니다.

GUI 확인:

```powershell
.\.venv\Scripts\python.exe -m admin_gui.main
```

Agent가 등록되고 2초마다 CPU·RAM·네트워크 값이 전송됩니다. 네트워크는 첫 샘플에서 결측, 다음 샘플부터 Mbps로 표시합니다. 종료는 Ctrl+C입니다. 원격 종료·재부팅을 실행하는 코드는 아직 없습니다.

## 설정

```powershell
.\.venv\Scripts\python.exe -m agent.main --host localhost --port 5000 --pc-name TEAM-B-PC --live
```

| 옵션 | 기본값·동작 |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `5000` |
| `--ca-file` | 신뢰할 서버 인증서 또는 CA PEM 파일. 토큰 파일과 함께 지정 |
| `--token-file` | 해당 Agent의 토큰 텍스트 파일. 파일 내용은 로그에 출력하지 않음 |
| `--pc-name` | Windows 호스트 이름 |
| `--pc-id` | 생략하면 저장된 UUID 사용. 명시하면 이번 실행만 덮어쓰며 저장 파일은 변경하지 않음 |
| `--id-file` | 저장소의 `runtime/agent-id.json`. 여러 설치를 구분할 때 별도 경로 지정 |
| `--log-file` | 저장소의 `runtime/logs/agent.log` |
| `--live` | 지속 수집·전송. 생략하면 등록 확인 후 종료 |

기존 수동 PC ID를 계속 사용하려면 동일한 `--pc-id`를 계속 지정합니다. ID 자동 저장 방식으로 전환하면 이전 수동 ID와 다른 PC로 등록될 수 있습니다.

## EC2 연결 (통합 Agent)

기존 프로젝트 Agent에 PC-02 배포본의 TLS·토큰 연결 기능을 통합했습니다. 별도 Agent 코드 복사본은 필요하지 않습니다. A에게 받은 서버 인증서와 해당 PC 토큰을 Git에서 제외된 `secrets/`에 보관합니다. 아래 파일명은 로컬 설정 예시이며 인증정보는 저장소에 포함하지 않습니다.

### PC별 최초 설정: 기존에 받은 토큰 그대로 사용

친구가 배포한 PC-01·PC-02·PC-03의 인증정보를 그대로 사용합니다. 새 토큰을 생성하지 않습니다. 각자 GitHub에서 같은 코드를 받은 뒤, 자신에게 배정된 배포본에서 인증서와 토큰 파일만 옮깁니다.

1. 저장소 루트에 `secrets/` 폴더를 만듭니다.
2. 배포본의 `server.crt`를 `secrets/server.crt`에 복사합니다.
3. **자신의 PC용** `secrets/token.txt`를 저장소의 `secrets/agent-token.txt`에 복사합니다.
4. 아래 실행 명령의 `--pc-id`를 자신에게 배정된 ID로 지정합니다.

| 받은 배포본 | 실행할 ID | 사용할 토큰 |
|---|---|---|
| PC-01 | `--pc-id PC-01` | PC-01 배포본의 토큰 |
| PC-02 | `--pc-id PC-02` | PC-02 배포본의 토큰 |
| PC-03 | `--pc-id PC-03` | PC-03 배포본의 토큰 |

모든 PC의 로컬 파일 이름은 `secrets/agent-token.txt`로 같아도 됩니다. **파일 내용은 각자 받은 토큰이어야 하며 PC ID와 토큰의 짝이 맞아야 합니다.** `secrets/`는 Git에서 제외됩니다. 토큰을 코드·문서·PR에 붙여 넣지 않습니다. 배포본의 별도 Agent 코드는 실행하지 않고 이 저장소의 Agent를 사용합니다.

### 실행

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 32.236.13.91 --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --pc-id PC-02 --live
```

등록만 확인하려면 `--live`를 생략합니다. 다른 PC에는 해당 PC의 ID와 토큰을 사용합니다. 서버가 토큰을 `PC-02`에 매핑했다면 자동 생성 UUID 대신 `--pc-id PC-02`를 지정해야 합니다.

- 외부 연결은 `--ca-file`과 `--token-file`이 모두 필요합니다. 로컬 개발용 평문 TCP는 두 옵션을 모두 생략합니다.
- TLS 1.2 이상을 사용하며 CA·인증서 유효성·서버 이름을 검증합니다. IP로 접속하면 인증서에 해당 IP가 포함되어야 합니다.
- TLS 연결 후 기존 `REGISTER` 메시지에 `token`을 추가합니다. `REGISTER_RESULT`의 성공 여부와 응답 PC ID를 확인합니다.
- 인증서 검증 실패·TLS 프로토콜 오류·등록 거절은 종료 코드 2로 종료합니다. 연결 단절은 다시 TLS 연결·등록을 수행하며 재접속 간격과 ID 유지·로그 정책은 그대로입니다.
- 현재 저장소의 서버·GUI는 여전히 로컬용입니다. Agent의 EC2 접속 지원이 로컬 서버·GUI의 TLS 지원을 의미하지는 않습니다.

## 재접속·로그

- 연결 실패·서버 종료 등 네트워크 장애는 3·6·12·24·30초로 재시도 간격을 늘리고 최대 30초를 유지합니다. 10초 이상 유지된 세션 후 장애는 3초부터 다시 시작합니다.
- 등록 거절·잘못된 프로토콜은 종료 코드 2로 종료합니다. 중복 ID나 메시지 규격을 수정한 뒤 재실행합니다.
- 로그는 콘솔과 파일에 남습니다. 파일은 1MB마다 순환하며 이전 파일 3개까지 보관합니다. 원시 메시지·토큰·개인키를 로그에 기록하지 않습니다.
- 단절 중 측정값을 쌓아 재전송하지 않고, 복구 시 새 샘플과 새 네트워크 기준값으로 시작합니다.
- CPU·RAM·네트워크 수집 실패는 해당 필드를 null로 보내고 다음 측정에서 복구를 시도합니다.

## PC 3대 대신 로컬에서 Agent 3개 테스트

각각 별도 터미널에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --pc-id test-pc-1 --pc-name TEST-1 --log-file runtime/logs/test-1.log --live
.\.venv\Scripts\python.exe -m agent.main --pc-id test-pc-2 --pc-name TEST-2 --log-file runtime/logs/test-2.log --live
.\.venv\Scripts\python.exe -m agent.main --pc-id test-pc-3 --pc-name TEST-3 --log-file runtime/logs/test-3.log --live
```

GUI에 세 행이 나타나는지, 하나를 종료해도 나머지가 유지되는지 확인합니다. 모두 같은 PC의 실제 측정값이므로 실제 PC 3대의 성능·연결 검증을 대신하지 않습니다. 같은 로그 파일로 여러 프로세스를 실행하지 마세요.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `python`을 찾지 못함 | Python 설치 후 터미널 재실행 또는 설치 경로의 python.exe 사용 |
| `No module named psutil` | 위 가상환경 Python으로 requirements.txt 설치 |
| 연결 실패 반복 | 서버 실행 여부·주소·포트 확인. 로컬 서버는 루프백 접속만 지원하며 EC2에는 위 TLS 명령 사용 |
| TLS 인증서 검증 실패 | CA 파일·인증서 유효기간·접속 IP 또는 도메인과 인증서 이름 일치 여부 확인 |
| TLS/token 설정 오류 | 인증서·토큰 파일이 존재하고 두 옵션을 함께 지정했는지 확인 |
| 등록·프로토콜 오류 | 중복 실행·중복 ID와 A의 메시지 규격 확인 |
| ID 파일 오류 | 파일을 덮어쓰지 않고 종료함. 백업 복원 또는 기존 ID를 `--pc-id`로 지정 |
| GUI에 이전 수동 ID와 새 UUID가 함께 표시 | 이전 행은 오프라인 기록. 같은 ID를 쓰려면 `--pc-id` 유지 |

## 검증 및 A·C 인계

수행한 테스트와 범위는 [로컬 검증 기록](agent-validation.md)에 정리했습니다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_reconnect
.\.venv\Scripts\python.exe -m tests.smoke_live
.\.venv\Scripts\python.exe -m tests.smoke_tls
```

재접속 검증은 별도 임시 포트에서 서버를 세 번 재시작하고 Agent 프로세스·PC ID 유지 및 실제 측정값 복구를 확인합니다. 중복 ID 거절 시 무한 재시도하지 않는지도 확인합니다. 파일은 `runtime/logs/`에 남습니다.

B가 인계할 것: [측정 규격](metrics-spec.md), 재접속 결과, Agent 실행 명령, null 처리와 PC ID 유지 방법. 테스트용 PC ID 예시 외 실제 PC ID·호스트 이름이 든 로그는 공유 전에 검토합니다.

A에게 받을 것: EC2 주소·포트, 신뢰할 CA·서버 이름, PC ID에 매핑한 토큰, 등록 응답 규격, Heartbeat 규격. TLS·토큰 인증 요청은 구현되어 있으며 Heartbeat·원격 제어·Windows 부팅 자동 실행은 아직 없습니다. `smoke_tls`는 OpenSSL(PATH 또는 Git for Windows)을 사용해 임시 인증서를 생성하고 TLS 등록·토큰 거절·실제 측정값·연결 복구 후 재인증을 확인합니다.
