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

로컬 자동 검증과 실제 EC2 검증 결과는 구분합니다. 아래 2026-11-09 운영 검증에서 최신 서버 배포와 실제 PC 3대의 접속·장애 분리·재접속을 확인했습니다. 보안 그룹과 AWS Budgets 설정은 AWS 관리 인증이 없어 확인하지 못했습니다.

SQLite·이력·경고·원격 종료/재부팅은 후속 주차 범위입니다. 기존 계획서의 체크박스는 현장 검증 없이 일괄 완료 처리하지 않습니다.

## 2026-11-09 EC2 배포·운영 검증 결과

- 인스턴스: `i-0d5ce99f9135b4eea`, 시드니 `ap-southeast-2`, 고정 접속 주소 `13.238.97.144`.
- 코드 경로: `/home/ubuntu/cloudnet-direct`. 배포 전 백업: `/home/ubuntu/cloudnet-backups/before-week2-20261109T060030Z.tar.gz` (접근 제한 적용, 인증정보 포함이므로 외부 공유 금지).
- 기존 인증 파일을 해시 비교해 변경되지 않았음을 확인. 인증서·토큰 재생성 없음.
- EC2의 별도 루프백 TLS 서버에서 기존 인증정보로 3개 세션·중복 ID·다른 PC 토큰 거절·Heartbeat 만료·나머지 연결 유지·동일 ID 복구 통과. 재현: `.venv/bin/python -m tests.smoke_server_deployed`. 이 테스트는 운영 5000번 세션을 건드리지 않음.
- 검증된 코드를 운영 서버에 배포. `cloudnet-control-launcher.service` 상태 active, 포트 5000에서 TLS/auth: True 확인.
- 운영 PC-01·PC-02·PC-03 모두 자동 재접속 및 실제 METRICS 수신. 서로 다른 세 네트워크에서 연결된 것을 소켓 상태로 확인.
- Windows의 실제 Dashboard 수신 작업으로 관리자 인증·세 PC 값 수신·측정 시각 갱신 확인. 화면 위젯을 직접 눈으로 확인한 결과와는 구분.
- 실제 로컬 PC-01 Agent 프로세스 종료 후 PC-01만 오프라인, PC-02·PC-03 온라인 유지 및 측정 시각 갱신 확인. 기존 ID·인증정보로 PC-01 재실행 후 세 PC 모두 온라인 복구.
- OS UFW 비활성 확인. AWS 보안 그룹 이름은 `launch-wizard-2`이지만 인바운드 규칙은 별도 콘솔 확인 필요. EC2 IAM 역할과 로컬 AWS 프로필이 없어 보안 그룹·Budgets·비용 알림을 조회하거나 변경하지 않음.

## 서버 시작·종료·로그

서비스 등록 후에는 아래 명령을 **SSH 접속한 Ubuntu 창**에서 사용합니다. 같은 포트로 Python 서버를 추가 실행하지 않습니다.

```bash
sudo systemctl status cloudnet-control-launcher.service --no-pager
sudo systemctl stop cloudnet-control-launcher.service
sudo systemctl start cloudnet-control-launcher.service
sudo systemctl restart cloudnet-control-launcher.service
journalctl -u cloudnet-control-launcher.service -n 50 --no-pager
```

SSH/PowerShell 창을 닫아도 이 서비스는 계속 실행됩니다. 오류 종료 시 재시작하지만 EC2 부팅 자동 시작은 설정하지 않았습니다. EC2 중지·종료는 위 서버 중지와 별도이며, 비용·디스크·탄력적 IP에 영향을 주므로 별도 관리합니다.

## 콘솔에서 확인할 남은 항목

- EC2 → 보안 그룹 `launch-wizard-2`: SSH 22번은 관리자 현재 공인 IP `/32`, TCP 5000번은 필요한 팀원 현재 공인 IP `/32`만 허용하는지 확인. 의도치 않은 전체 공개 IPv4/IPv6 규칙 확인.
- Billing and Cost Management → Budgets: 프로젝트 예산 금액·이메일 수신자·실제/예측 지출 알림과 이메일 수신 상태 확인. 설정되지 않았으면 계정 소유자가 원하는 예산 금액과 알림 이메일로 생성.
- Elastic IP: 프로젝트 인스턴스에 연결 상태 확인. 실습 종료 후 더 이상 필요하지 않을 때 인스턴스 사용 여부를 확인하고 연결 해제·주소 릴리스. 릴리스한 주소는 다시 확보된다고 보장되지 않음. EC2 중지만으로 EBS와 탄력적 IP 비용이 모두 없어지지는 않음.
