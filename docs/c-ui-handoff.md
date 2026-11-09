# C UI 변경 인수인계 — A·B 공유용

현재 주소: `13.238.97.144:5000`, 기존 인증서 검증 이름: `32.236.13.91`.
인증서·토큰 내용은 변경하거나 저장소에 포함하지 않았습니다.
PC-03 Agent는 등록 및 측정 전송 시작을 로컬 로그에서 확인했습니다. 관리자 목록의 접속 여부는 A 확인이 필요합니다.

## A에게 전달

- 최신 main에는 GUI와 프로세스 명령 중계가 포함됩니다. 기존 인증서·개인키·auth.json을 유지하고 서버 코드 업데이트·재시작 후 확인하세요. 인증정보를 새로 생성할 필요는 없습니다.
- A의 PC별 Agent 토큰 인증, 별도 관리자 토큰, Heartbeat 타임아웃·STATUS_UPDATE 알림을 보존했습니다. 실행 옵션은 `--tls-cert`, `--tls-key`, `--auth-file`입니다.
- GUI에는 Agent 토큰과 다른 관리자 토큰 파일 및 신뢰 인증서가 필요합니다.
- 모니터링은 LIST_DEVICES 1초 조회, 프로세스 기능은 별도의 인증된 관리자 연결을 사용합니다. 프로세스 목록은 모든 페이지를 모은 후 갱신합니다.
- 서버 중계는 TLS 관리자·활성 TLS Agent 세션·명령 ID를 검증하고 결과를 요청한 관리자에게만 반환합니다. 실제 종료 전 보호 정책·PID 재사용 검증은 B Agent가 수행합니다.
- 배포 후 실제 PC 3대 목록, 관리자 인증 실패, 서버 복구, LIST_PROCESSES 분할 응답과 STOP_PROCESS 결과 전달을 확인하세요. 영속 감사 이력은 후속 작업입니다.

관리자 실행 예시 (관리자 토큰 경로는 실제 위치에 맞춤):

```powershell
.\.venv\Scripts\python.exe -m admin_gui.main --host 13.238.97.144 --port 5000 --server-name 32.236.13.91 --ca-file secrets/server.crt --token-file secrets/admin-token.txt
```

## B에게 전달

- B의 COMMAND/COMMAND_RESULT 규격으로 GUI를 연동했습니다. PID·이름·사용자·메모리·can_stop·보호 이유를 표시하고 확인창 후 PID와 create_time을 그대로 전달합니다.
- 보호 행·목록 수신 중·불완전·단절 상태에서는 종료를 막습니다. 결과 수신 후 새 명령 ID로 전체 목록을 재조회하며 종료 명령 자체는 자동 재전송하지 않습니다.
- 일반 측정에는 현재 명령을 유지하세요. 프로세스 조회·보호 종료를 테스트할 Agent에만 `--enable-process-control`을 추가해 재실행하세요. 기본 비활성은 유지했습니다.
- 각자 배정 ID와 기존 토큰을 사용하세요. 같은 파일명이어도 토큰 내용은 PC마다 다릅니다.
- null 필드·시스템 PID 생성 시각 0을 처리합니다. TERMINATION_REQUESTED를 종료 완료로 표시하지 않습니다.

PC-03 프로세스 기능 테스트용 명령:

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 13.238.97.144 --port 5000 --pc-id PC-03 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --server-name 32.236.13.91 --live --enable-process-control
```

## 검증 범위

전체 단위·서버 통합 테스트 87개 통과. 실제 GUI·임시 TLS 서버·실제 B Agent를 연결해 다중 페이지 목록,
보호 행 버튼 비활성화, 테스트용 자식 프로세스 종료 후 자동 새로고침을 확인했습니다.
샘플 화면은 실제 GUI 구조를 공유하고 네트워크 없이 정상·데이터 없음·오프라인 예시를 표시합니다.
PC 종료·재부팅은 확인창만 제공하며 실제 동작은 미연동입니다.
이 문서는 공유용이며 A·B에게 자동 메시지를 보내지 않았습니다.
