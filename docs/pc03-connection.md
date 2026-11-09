# PC-03 서버 접속

현재 접속 주소는 탄력적 IP `13.238.97.144:5000`, 이 PC의 Agent ID는 `PC-03`입니다.
기존 인증서를 유지하므로 TLS 검증 이름은 `--server-name 32.236.13.91`로 지정합니다.
인증서 검증은 활성 상태로 유지하며, 인증서·토큰 파일 내용은 변경하지 않습니다.

이 작업 폴더에는 `secrets/server.crt`와 `secrets/agent-token.txt`가 있으므로 다음 명령을 사용합니다.
팀 안내의 `server.crt`·`secrets/token.txt`는 실제 파일 위치에 맞게 지정하면 됩니다.

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --pc-id PC-03 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --server-name 32.236.13.91 --live --log-file runtime/logs/pc03-ec2-live.log
```

2026-11-09 로컬 확인: `Registered PC PC-03 at 13.238.97.144:5000`와 `Metric transmission started` 확인.
별도 PowerShell 창에서 실행을 유지하며, 중지할 때는 해당 창에서 Ctrl+C를 누릅니다.
관리자 측 목록에서 보이는지는 별도로 관리자에게 확인받아야 합니다.
이 실행은 팀 안내에 맞춘 측정 모드이며, 원격 프로세스 제어 활성 옵션은 추가하지 않았습니다.

## 프로세스 조회·보호 종료 활성화

PC-03에서 기존 Agent를 Ctrl+C로 중지한 뒤, 저장소 루트의 `Start PC-03 Process Control.cmd`를 실행합니다. 이 파일은 기존 인증서·PC-03 토큰으로 `--enable-process-control`을 추가해 Agent를 실행합니다. 배포본의 `server.crt`·`secrets/token.txt`와 저장소의 `secrets/server.crt`·`secrets/agent-token.txt` 경로를 모두 지원합니다. PC-01 담당자는 `Start PC-01 Process Control.cmd`를 자신의 PC에서 사용합니다.

두 실행 파일은 해당 PC에 최신 Agent 코드·의존성·기존 PC별 인증정보가 있을 때 동작합니다. 이 PC에서 다른 PC의 실행 파일을 대신 실행하면 원격 PC 설정이 바뀌는 것이 아니라 잘못된 ID 등록이 시도되므로 실행하지 않습니다. 관리자 GUI에는 원격 활성화 기능이 없습니다. 각 담당자가 재실행한 뒤 관리자가 목록 조회로 확인해야 합니다.
