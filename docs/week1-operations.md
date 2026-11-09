# A 담당 1주차 운영 마감

갱신일: 2026-11-09. A 서버 운영 설정·비용 알림·인증서·종료 절차입니다. B·C 코드와 기존 인증정보를 변경하지 않습니다.

## 현재 완료 상태

| 항목 | 상태 |
|---|---|
| EC2·Python·TLS 서버 | 배포·외부 TLS 인증 확인 완료 |
| Agent/Admin 권한·PC별 토큰 매핑 | 구현 및 실제 클라이언트 연동 확인 |
| 고정 접속 IP | 13.238.97.144, EC2 메타데이터·외부 연결 확인 |
| TLS 검증 식별자 | 기존 SAN 32.236.13.91 유지, --server-name으로 검증 |
| 현재 메시지 규격 | protocol.md에 등록·목록·측정·Heartbeat·상태·프로세스 명령 정리 |
| 실행·종료·재접속 안내 | ec2-direct.md와 아래 절차 작성 완료 |
| 보안 그룹 제한 | 실제 API 조회 완료. sg-03705ec15acec31fe의 SSH 1개·서비스 3개 /32 규칙 정상. 변경 불필요 |
| AWS Budgets·알림 | CloudNet-Control-Monthly, 월 USD 50, neonek0629@gmail.com 수신자와 알림 3개 적용·재조회 완료 |
| 예산 알림 수신 | AWS API상 수신자 등록 확인. 실제 메일 수신·본인 확인은 사용자 점검 대기 |

실제 AWS 설정을 조회하지 않은 항목은 완료로 표시하지 않습니다.

## 주소와 인증서

실제 접속은 13.238.97.144, TLS 검증 이름은 32.236.13.91입니다. 두 값을 분리하는 기능은 B·C가 구현했습니다. 기존 인증서·토큰·auth.json을 재생성하거나 인증서 검증을 끄지 않습니다. SSH는 known_hosts의 기존 키를 HostKeyAlias로 별도 검증합니다.

초기 계획의 '새 IP를 인증서 SAN에 반영' 대신 '기존 인증서 식별자 유지·접속 주소 분리'라는 현재 운영 방식을 사용합니다. 인증서 갱신이 필요해지면 A가 배포 계획을 제시하고 B·C가 클라이언트 적용을 담당합니다.

## 인바운드 설정안

2026-11-09 현재 SSH·EC2 소켓에서 확인한 외부 주소입니다. 클라이언트 공인 IP는 바뀔 수 있으므로 적용 시 확인합니다. 서버 EIP를 인바운드 소스로 넣지 않습니다.

| 포트 | 허용 소스 |
|---|---|
| TCP 22 | 관리자 125.240.65.158/32 |
| TCP 5000 | 관리자 125.240.65.158/32, PC-02 39.114.149.86/32, PC-03 125.183.84.4/32 |

대상: i-0d5ce99f9135b4eea / ap-southeast-2 / 관측한 그룹 launch-wizard-2. 좁은 규칙을 추가해도 기존 전체 공개 규칙이 남아 있으면 접근은 제한되지 않습니다. [AWS 보안 그룹 설명](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/creating-security-group.html)

## AWS CloudShell 적용

현재 AWS CLI 프로필 CloudNet Control로 기존 프로젝트 844139705430 인증을 완료했습니다. AWS MCP는 공백 없는 내부 별칭 CloudNet-Control-MCP로 같은 로그인 세션을 사용합니다. 아래 CloudShell 절차는 이 PC 인증을 사용할 수 없을 때의 대체 방법입니다. SSH 키와 AWS API 인증은 별개이며 비밀키를 채팅에 전달하지 않습니다.

1. AWS 콘솔에서 CloudShell을 엽니다.
2. Actions → Upload file로 ops/aws_week1.py를 올립니다.
3. 아래 미리보기 명령에 월 예산(USD)과 이메일을 입력합니다. 예산은 계정 전체 비용을 대상으로 하므로 다른 리소스도 포함합니다.

```bash
python3 aws_week1.py --admin-ip 125.240.65.158 --client-ip 39.114.149.86 --client-ip 125.183.84.4 --monthly-usd YOUR_MONTHLY_USD --email YOUR_EMAIL
```

출력의 인스턴스·EIP·추가/삭제 규칙·예산·수신자를 확인한 후 같은 명령 끝에 --apply를 추가합니다.

```bash
python3 aws_week1.py --admin-ip 125.240.65.158 --client-ip 39.114.149.86 --client-ip 125.183.84.4 --monthly-usd YOUR_MONTHLY_USD --email YOUR_EMAIL --apply
```

기능과 범위:

- EIP가 대상 인스턴스에 연결됐는지 API로 검증.
- 보안 그룹이 다른 자원과 공유되거나 여러 그룹이 붙어 있으면 중단.
- 전체 트래픽·넓은 포트 범위는 자동 변경하지 않고 사전 중단.
- 전용 그룹의 정확한 22·5000번 규칙에 한해 /32 허용을 먼저 추가한 뒤 불필요한 IPv4·IPv6·그룹 소스 규칙 제거.
- 다른 포트·아웃바운드·EC2 실행 상태·인증정보·B·C 코드는 변경하지 않음.
- 월 COST 예산 CloudNet-Control-Monthly와 실제 지출 80%·100%, 예측 100% 알림 준비.
- 동명 예산의 금액·범위가 다르면 덮어쓰지 않음. 기존 알림 수신자 삭제 없음.
- 변경 전 ~/cloudnet-week1-시각/before.json, 완료 후 after.json 저장.

API 작업은 하나의 트랜잭션이 아닙니다. 중간 오류 시 출력과 백업으로 성공한 단계·남은 단계를 확인합니다. 권한 부족은 해당 계정에서 해결하며 SSH 키로 우회하지 않습니다.

알림은 초과 지출을 차단하거나 EC2를 자동 중지하지 않습니다. 수신자 확인 이메일 절차를 완료하고 실제 수신 여부를 별도로 확인해야 합니다. [예산 생성](https://docs.aws.amazon.com/cost-management/latest/userguide/create-cost-budget.html), [Budgets 동작](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)

적용 후 새 관리자 SSH 연결, PC별 TLS 접속·GUI 갱신, 보안 그룹·예산·수신자 상태를 확인합니다. 그 결과를 받은 뒤 위 실제 설정 완료 상태를 갱신합니다.

## 종료와 IP 유지

| 동작 | 영향 |
|---|---|
| SSH/PowerShell 닫기 | systemd 서버·EC2는 계속 실행 |
| systemctl stop | 서버만 중지, EC2·EIP 유지 |
| Python 서버·서비스 재실행 | 현재 EIP 유지 |
| EC2 Reboot | 인스턴스 재부팅, EIP 유지. 현재 서버는 부팅 자동 시작이 아니므로 상태 확인·수동 시작 필요 |
| EC2 Stop → Start | 연결 종료, 연결된 EIP 유지. 시작 후 서버 수동 시작 필요 |
| EIP 연결 해제 | 기존 EIP로 접속 불가, EIP는 계정에 남음 |
| EIP 릴리스 | 고정 주소 반납, 같은 주소 재확보 보장 없음 |

현재 서비스는 오류 종료 시 재시작하지만 EC2 부팅 자동 시작은 설정하지 않았습니다. 사용자가 원하는 종료 방식을 바꾸지 않도록 임의로 enable하지 않습니다.

잠시 실습을 마칠 때: 팀원 사용 종료 확인 → 서버 중지 → AWS 콘솔에서 EC2 중지 → stopped 확인. Agent·GUI는 각 PC에서 따로 종료합니다. 직접 실행한 Agent는 Ctrl+C, 백그라운드 Agent는 실제 PID·명령을 확인한 뒤 종료합니다.

완전히 정리할 때: 필요한 코드·데이터 백업 → 팀원 사용 종료 → 불필요한 EC2·EBS·스냅샷 각각 확인 후 정리 → EIP 연결 해제·릴리스 → 남은 자원과 청구 확인. 이 문서 정리 작업에서는 인스턴스 종료·볼륨 삭제·EIP 반납을 실제 수행하지 않습니다.

## 비용

EC2 중지 후에도 EBS·스냅샷·할당된 EIP 등의 비용은 남을 수 있습니다. EIP는 사용·미사용 모두 과금 대상입니다. 공개 IPv4 기본 단가는 시간당 USD 0.005이며 다른 자원은 별도입니다. 계정 혜택·크레딧 적용 여부는 해당 계정의 청구 화면에서 확인합니다. [AWS EIP 설명](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html), [VPC 요금](https://aws.amazon.com/vpc/pricing/)

예산 알림은 즉시 차단 장치가 아니며 반영·전달 지연이 있을 수 있습니다. 예산액·수신자가 정해지기 전 임의의 값으로 생성하지 않습니다.

## 2026-11-09 실제 완료 기록

- 보안 그룹 sg-03705ec15acec31fe는 이미 원하는 규칙과 일치해 인바운드 추가·삭제를 하지 않았습니다.
- 월 예산 CloudNet-Control-Monthly: MONTHLY / COST / USD 50. 실제 지출 80%(40달러)·100%(50달러), 예측 100%(50달러), 이메일 neonek0629@gmail.com 등록을 API로 확인했습니다.
- AWS CLI 2.39.6, CloudNet Control 인증 확인. AWS MCP 프록시 1.8.0, 기본 스킬 25개 설치, 원격 카탈로그 121개 조회, MCP 초기화·도구 목록 조회 성공.
- MCP 프록시가 공백 구분 목록을 사용하므로 같은 로그인 세션을 가리키는 내부 별칭만 추가했습니다. 기존 표시용 프로필·인증서·토큰은 유지합니다.
- 조회 파일: C:/Users/user/cloudnet-week1-20261109T075841588706Z/before.json 및 after.json. 비밀키는 없지만 이메일 등 운영 정보가 있어 비공개로 보관합니다.
- 사용자 메일함의 실제 확인/알림 메일 수신은 검증 대기입니다. API 성공을 실제 수신 성공으로 표시하지 않습니다.
- 새 Codex 세션에서 AWS MCP가 로드됩니다. 원래 폴더의 C GUI 브랜치는 유지했고 B·C 소스는 변경하지 않았습니다.
- 최종 SSH 접속 정상. 서버는 이번 AWS 설정 적용 전인 07:50:31 UTC에 정상 중지됐으며 현재 5000번 리스너는 없습니다. 기존 중지 상태를 유지했습니다.
