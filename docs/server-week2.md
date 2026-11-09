# A 담당 서버 2주차 결과 및 인계

작성일: 2026-11-09. 범위: 서버 다중 연결·인증·세션·Heartbeat·관리자 상태 전달.

GitHub 팀원 코드 병합 후 전체 자동 테스트 **71개 통과, skip 없음**. B의 선택적 Heartbeat·프로세스 제어 Agent 코드를 보존하고, IP 변경 후 기존 인증서를 검증하는 --server-name 옵션을 통합했습니다. 프로세스 명령의 서버 라우팅·GUI 제어는 별도 미구현입니다. 현재 운영 접속 주소는 탄력적 IP **13.238.97.144**이며 기존 인증서와 토큰을 유지합니다. GitHub 업데이트가 EC2의 서버 코드 배포를 자동으로 수행하지는 않습니다.

## 구현한 기능

- 연결별 스레드, TLS 1.2 이상, PC별 Agent 토큰과 별도 Admin 토큰 검증.
- UTF-8 JSON과 4바이트 big-endian 길이 헤더, 최대 65,536바이트 제한.
- 등록 전 요청·역할 위반·잘못된 JSON·잘못된 길이·중복 PC ID 거절.
- 잠금으로 세션 등록·해제 보호, 연결별 송신 잠금으로 프레임 혼합 방지.
- HEARTBEAT 및 METRICS 기반 생존 확인, 기본 15초 만료와 개별 PC 오프라인 처리.
- 관리자 DEVICE_LIST 조회, 선택적 STATUS_UPDATE 구독과 연결별 알림 큐.
- 등록·해제·만료·규격 오류 서버 로그. 토큰과 수신 본문은 로그에 기록하지 않음.

## 로컬 재현

프로젝트 루트의 PowerShell에서:

```powershell
.\.venv\Scripts\python.exe -m server.main
.\.venv\Scripts\python.exe -m unittest tests.test_server_week2 tests.test_monitoring tests.test_protocol tests.test_security -v
```

서버와 테스트는 독립적으로 실행할 수 있습니다. 테스트는 임시 포트·소켓·인증 파일을 사용합니다. TLS 통합 테스트에는 OpenSSL이 필요하며 없으면 해당 클래스가 skip됩니다.

## 확인 항목

2026-11-09 로컬 Windows 가상환경에서 `python -m unittest discover -s tests -v` 실행: **49개 통과, skip 없음**. 검증에는 임시 인증서를 사용하는 실제 TLS 서버·Agent 프로세스 및 GUI 수신 작업 테스트가 포함됩니다. 실제 PC 3대와 EC2 현장 시연 결과는 아닙니다.

| 항목 | 자동 검증 |
|---|---|
| TLS의 Agent 3개와 관리자 동시 연결 | tests.test_security |
| 토큰 누락·오류·다른 PC 토큰·역할 오용 거절 | tests.test_security |
| 인증서 신뢰·서버 이름 검증, 평문 접속 거절 | tests.test_security |
| 분할 수신·연속 메시지·잘못된 길이 | tests.test_protocol, tests.test_server_week2 |
| 중복 ID가 원래 세션에 영향 없음 | tests.test_monitoring |
| 한 PC 해제 후 다른 2개 온라인 유지 | tests.test_monitoring, tests.test_server_week2 |
| Heartbeat 유지·만료·동일 ID 재연결 | tests.test_server_week2 |
| 불완전 프레임으로 타임아웃 연장 불가 | tests.test_server_week2 |
| 관리자 상태 구독·역할별 메시지 제한·동시 송신 | tests.test_server_week2 |
| 실제 Agent 수집·GUI 수신 작업의 TLS 연결 | tests.test_security |

## B·C 인계

B: 기존 2초 METRICS는 그대로 사용합니다. 수집이 멈추어도 연결을 유지하려면 5초 간격 HEARTBEAT를 보내세요. 등록 ID와 같아야 하며 ACK는 없습니다. 여러 송신 스레드를 쓰면 B 측에서도 송신 잠금이 필요합니다.

C: 기존 1초 LIST_DEVICES 조회는 그대로 사용합니다. 상태 push를 쓰려면 관리자 REGISTER에 subscribe_status=true를 추가하고 STATUS_UPDATE와 DEVICE_LIST를 구분해 처리하세요.

실제 외부 연결에는 [EC2 배포 안내](ec2-direct.md)의 인증서·역할별 토큰을 사용합니다.

## 완료 범위와 남은 현장 확인

코드와 로컬 자동 검증은 실제 EC2 및 서로 다른 Windows PC 3대의 현장 검증과 구분합니다. 현장에서는 서버 시작 로그의 TLS/auth: True, 각 PC 등록, 관리자 목록, 한 PC 강제 종료 후 나머지 연결 유지, 동일 ID 재접속을 확인하고 결과를 기록해야 합니다. 이 작업에서는 AWS 자원·배포·보안 그룹을 변경하지 않았습니다.

SQLite·이력·경고·원격 종료/재부팅은 후속 주차 범위입니다. 기존 계획서의 체크박스는 현장 검증 없이 일괄 완료 처리하지 않습니다.
