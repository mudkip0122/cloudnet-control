# EC2 연결·서버 운영 안내

갱신일: 2026-11-09. A 운영 문서입니다. B·C 코드와 기존 인증정보를 수정하지 않습니다.

2026-11-12 후속: A fdb7bb2의 server/common을 배포하고 서버를 시작했습니다. 감사 DB 쓰기 경로와 systemd drop-in, 실제 PC-01/02 검증 및 PC-03 대기 상태는 [3주차 운영 결과](server-week3.md)를 우선합니다. 아래 과거 서비스 중지 안내는 당시 기록입니다.

| 항목 | 현재 값 |
|---|---|
| 인스턴스·리전 | i-0d5ce99f9135b4eea / ap-southeast-2 |
| 탄력적 IP·서비스 포트 | 13.238.97.144:5000 |
| TLS 인증서 검증 이름 | 32.236.13.91 |
| EC2 프로젝트 | /home/ubuntu/cloudnet-direct |
| 서비스 | cloudnet-control-launcher.service |

실제 접속 주소 --host와 인증서 검증 이름 --server-name을 구분합니다. 기존 인증서·토큰·auth.json을 재생성하지 않습니다. B main 53f2bbf부터 Agent도 --server-name을 공식 지원합니다.

## 1. Windows PowerShell에서 SSH 접속

```powershell
ssh -o HostKeyAlias=32.236.13.91 -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@13.238.97.144
```

HostKeyAlias는 기존 SSH 호스트 키 검증용입니다. TLS 인증서 검증과는 별개이며 SSH 개인키는 관리자만 보관합니다.

## 2. 접속한 Ubuntu에서 서버 관리

필요한 명령 하나를 선택합니다. 이번 AWS 설정 작업 전부터 서비스는 중지돼 있었고 그 상태를 유지했습니다.

```bash
sudo systemctl status cloudnet-control-launcher.service --no-pager
sudo systemctl start cloudnet-control-launcher.service
sudo systemctl restart cloudnet-control-launcher.service
sudo systemctl stop cloudnet-control-launcher.service
journalctl -u cloudnet-control-launcher.service -n 50 --no-pager
```

active (running)이면 추가 실행하지 않습니다. 같은 포트에 Python 서버를 또 실행하면 Address already in use가 납니다. SSH 창을 닫아도 서비스는 계속 실행됩니다. 오류 종료는 재시작하지만 EC2 부팅 자동 시작은 설정하지 않았습니다.

## 3. 별도 Windows PowerShell에서 PC-01 Agent

기존 PC-01 Agent를 먼저 종료합니다. 직접 실행한 창에서는 Ctrl+C, 백그라운드 프로세스는 실제 PID·명령을 확인하고 해당 Agent만 종료합니다. 과거 안내의 PID를 재사용하지 않습니다.

```powershell
cd "C:\Users\user\Desktop\CloudNet Control"
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --pc-id PC-01 --ca-file secrets/agent/server.crt --token-file secrets/agent/token.txt --server-name 32.236.13.91 --live --enable-process-control
```

B의 옵션을 사용하는 실행 예시입니다. 제어가 필요 없으면 --enable-process-control을 생략합니다. 별도 Heartbeat는 --heartbeat-interval 5, 기존 METRICS도 생존 신호로 인정됩니다. 다른 PC는 자신의 ID와 배정된 토큰 경로를 사용합니다.

## 4. 별도 Windows PowerShell에서 GUI

```powershell
cd "C:\Users\user\Desktop\CloudNet Control"
.\.venv\Scripts\python.exe -m admin_gui.main --host 13.238.97.144 --port 5000 --ca-file secrets/clients/admin/server.crt --token-file secrets/clients/admin/secrets/token.txt --server-name 32.236.13.91
```

의존성이 없으면 같은 폴더에서 설치합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

관리자 ZIP은 Agent와 별도 폴더에 풀고 requirements.txt가 있는 위치에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m admin_gui.main --host 13.238.97.144 --port 5000 --ca-file server.crt --token-file secrets/token.txt --server-name 32.236.13.91
```

GUI에는 --pc-id가 필요 없습니다. 관리자 토큰은 전체 PC에 대한 명령 권한을 주며 ZIP은 비공개로 전달하고 Git에 올리지 않습니다.

## 운영·배포

PROCESS_CONTROL_DISABLED는 대상 Agent의 제어 기능이 꺼진 상태입니다. 프로세스 목록·보호 종료 중계는 구현됐고 PC 종료·재부팅·영구 이력은 후속 범위입니다.

A는 server/와 필요한 common/만 백업 후 교체하고 서비스를 재시작합니다. agent/·admin_gui/를 임의로 덮어쓰지 않으며 인증 파일은 배포에서 제외합니다. GitHub 갱신과 EC2 배포는 별개입니다. [1주차 AWS 설정·비용·종료 절차](week1-operations.md)를 참고하세요.
