# EC2 직접 연결 (SSH 터널 없이)

## 현재 운영 주소 (2026-11-09 갱신)

탄력적 IP는 `13.238.97.144`입니다. 해당 주소의 5000번 포트에 기존 인증서·관리자 토큰으로 TLS 연결 및 인증 성공을 확인했습니다. 아래 최초 배포 예시의 `32.236.13.91`은 이전 주소입니다. 현재 접속에는 `--host 13.238.97.144 --server-name 32.236.13.91`을 사용합니다. 기존 인증서와 각 PC 토큰은 재생성하지 않습니다.

기존 배포 Agent 폴더에서 PC-02 실행 예시:

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --pc-id PC-02 --ca-file server.crt --token-file secrets/token.txt --server-name 32.236.13.91 --live
```

PC-03은 ID만 PC-03으로 바꾸고 본인의 기존 토큰 파일을 사용합니다. `--server-name` 옵션을 인식하지 못하면 구버전 클라이언트이므로 연결 코드 업데이트가 필요합니다. 인증서 검증을 비활성화하지 않습니다. 팀원 네트워크에서 시간 초과가 나면 EC2 보안 그룹의 TCP 5000 허용 대상에 해당 팀원의 현재 공인 IP `/32`가 있는지 확인합니다.

---

Windows Agent 3대와 관리자 GUI가 TCP 5000번으로 EC2에 TLS 연결합니다.
SSH 22번과 관리자 PEM 키는 서버 관리 담당자만 사용합니다.
로컬 개발 명령은 그대로 동작합니다. 외부 접속과 외부 바인딩은 TLS와 인증 설정을 요구합니다.

## 1. 업데이트한 소스 업로드

GitHub에 반영되지 않은 로컬 수정본은 배포 압축파일로 전송합니다.
`dist/cloudnet-ec2.zip`에는 코드와 문서만 있으며 개인키나 인증정보를 포함하지 않습니다.
Windows PowerShell에서:

```powershell
cd 'C:\Users\user\Desktop\CloudNet Control'
scp -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" .\dist\cloudnet-ec2.zip ubuntu@32.236.13.91:~/cloudnet-ec2.zip
ssh -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@32.236.13.91
```

EC2에서 기존 서버는 Ctrl+C로 중지합니다. 새 소스는 별도 디렉터리에 풀어서 기존 설치를 보존합니다.

```bash
mkdir ~/cloudnet-direct
cd ~/cloudnet-direct
python3 -m zipfile -e ~/cloudnet-ec2.zip .
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sudo apt install -y openssl
.venv/bin/python -m server.provision --public-ip 32.236.13.91
```

`secrets/ec2`가 이미 있으면 생성 명령은 중단됩니다. 인증정보를 실수로 덮어쓰지 않도록 한 것이므로
최초 한 번만 실행합니다. 생성 과정이 실패했다면 원인을 먼저 확인하고 새 `--output` 디렉터리를 사용합니다.
이 경우 아래 실행 명령의 경로도 해당 디렉터리로 바꿉니다.

## 2. 서버 실행

```bash
cd ~/cloudnet-direct
.venv/bin/python -m server.main --host 0.0.0.0 --port 5000 --tls-cert secrets/ec2/server.crt --tls-key secrets/ec2/server.key --auth-file secrets/ec2/auth.json
```

`TLS/auth: True`를 확인하고 창을 유지합니다. 기존 SSH 터널은 새 TLS 서버 접속에는 필요 없습니다.
보안 그룹은 SSH 22번을 관리자의 IP에만 허용하고 TCP 5000번을 각 PC 공인 IP `/32`에 허용합니다.
전체 공개 규칙은 제거합니다. 팀원에게 SSH 키를 전달하지 않습니다.

## 3. 클라이언트 파일 가져오기

Windows PowerShell의 프로젝트 루트에서:

```powershell
New-Item -ItemType Directory -Force .\secrets\clients | Out-Null
scp -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@32.236.13.91:~/cloudnet-direct/secrets/ec2/PC-01.zip .\secrets\clients\
scp -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@32.236.13.91:~/cloudnet-direct/secrets/ec2/PC-02.zip .\secrets\clients\
scp -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@32.236.13.91:~/cloudnet-direct/secrets/ec2/PC-03.zip .\secrets\clients\
scp -i "$env:USERPROFILE\.ssh\sheep_jae0_key.pem" ubuntu@32.236.13.91:~/cloudnet-direct/secrets/ec2/admin.zip .\secrets\clients\
```

- 본인: PC-01.zip, admin.zip 보관
- 39.114.149.86 PC: PC-02.zip만 전달
- 125.183.84.4 PC: PC-03.zip만 전달

ZIP에는 해당 역할/PC의 토큰이 들어 있으므로 비공개로 전달하고 GitHub에 업로드하지 않습니다.
서버의 `server.key`, `auth.json`은 EC2에만 보관합니다.

각 ZIP은 별도 폴더에 압축 해제합니다. 각 PC에는 Python 3.11 이상과 tkinter가 필요합니다.
PowerShell에서 압축 해제한 폴더로 이동하고:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
.\run.ps1
```

`python` 명령이 인식되지 않으면 실제 설치된 Python 경로를 사용합니다.
스크립트 실행 정책이 차단하면 파일 내용을 확인한 후 같은 창에서
`Get-Content .\run.ps1`로 표시한 명령을 직접 실행할 수 있습니다.
본인 PC는 PC-01 폴더에서 Agent를, admin 폴더에서 GUI를 각각 실행합니다.
기존 Agent는 먼저 Ctrl+C로 중지합니다. 다른 PC는 Agent만 실행합니다.

## 4. 검증과 운영

- 관리자 GUI에서 PC-01, PC-02, PC-03의 온라인 상태와 갱신되는 실제 CPU/RAM 확인
- 한 Agent 종료 시 해당 행만 오프라인으로 바뀌는지 확인
- 잘못된 토큰/다른 PC의 토큰으로 등록이 거부되는지 확인
- 인증서 불일치 시 연결 실패 확인 (인증서 검증을 끄지 않음)
- 3대 연결을 검증한 뒤 systemd 서비스로 서버 등록

인증서는 365일 유효합니다. 자동 할당 퍼블릭 IP는 EC2 중지/시작 시 바뀔 수 있습니다.
주소가 바뀌면 인증서의 IP SAN과 클라이언트의 주소도 갱신해야 합니다.
공인 IP가 바뀐 클라이언트는 보안 그룹의 `/32` 규칙을 수정합니다.
EC2에 접속한 SSH 창을 닫으면 현재 수동 실행 서버가 종료될 수 있습니다.
아직 이력 저장, 원격 종료/재부팅은 구현되어 있지 않습니다.
