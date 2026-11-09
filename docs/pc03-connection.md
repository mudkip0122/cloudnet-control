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
