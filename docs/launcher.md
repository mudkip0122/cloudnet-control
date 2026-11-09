# 더블클릭으로 EC2·서버·GUI 시작 및 종료

프로젝트 루트의 `Launch CloudNet.cmd`는 관리자 본인 PC 전용입니다.
팀원은 기존 Agent만 실행하며 AWS 인증정보나 관리자 PEM을 받지 않습니다.

실행 순서:

1. 지정한 인스턴스의 시작·중지 권한을 실제 변경 없이 확인합니다.
2. EC2가 중지 상태라면 시작하고 현재 퍼블릭 IPv4를 조회합니다.
3. 기존에 신뢰한 SSH 호스트 키를 검증하고 EC2에 연결합니다.
4. `cloudnet-control-launcher.service`를 등록·실행합니다.
5. TLS와 관리자 인증을 검증하고 선택에 따라 PC-01 Agent와 최신 GUI를 실행합니다.
6. GUI의 X 또는 실행기의 종료 버튼을 누르면 본 실행기가 띄운 로컬 프로그램을 닫고,
   서버 서비스를 중지한 뒤 EC2의 `stopped` 상태를 확인합니다.

인스턴스를 삭제하는 TerminateInstances API는 사용하지 않습니다.
이미 실행 중인 인스턴스도 서버 관리에 성공한 뒤에는 이 실행기의 종료 대상이 됩니다.
그 전 설정 오류로 중단된 경우 기존 실행 중인 EC2를 임의로 중지하지 않습니다.

## 최초 한 번 설정

### AWS API 인증

PEM은 Ubuntu SSH 로그인용입니다. EC2를 시작·중지하는 AWS API 인증과는 별개입니다.
AWS 콘솔에 로그인한 것만으로 Windows 프로그램에 API 권한이 생기지 않습니다.

[AWS CLI v2 설치 안내](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)를 따라
Windows AWS CLI를 설치한 뒤, 계정에서 허용한 방식으로 프로필을 준비합니다.

IAM Identity Center를 사용하는 계정:

```powershell
aws configure sso --profile cloudnet
aws sso login --profile cloudnet
```

개인 실습 계정에서 IAM 사용자 액세스 키를 사용하는 경우:

```powershell
aws configure --profile cloudnet
```

입력할 키는 계정에서 발급한 IAM 액세스 키이며 PEM 파일이 아닙니다. 루트 액세스 키를 만들지 않습니다.
비밀 키는 이 PC에서만 입력하고 코드·채팅·ZIP에 넣지 않습니다.
프로필은 `%USERPROFILE%\.aws`에서 SDK가 읽으며 실행기 설정에는 프로필 이름만 저장됩니다.
SSO 세션이 만료되면 `aws sso login --profile cloudnet`이 다시 필요합니다.

필요한 최소 정책 예시입니다. ACCOUNT_ID와 INSTANCE_ID를 실제 값으로 바꿉니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["ec2:StartInstances", "ec2:StopInstances"],
      "Resource": "arn:aws:ec2:ap-southeast-2:ACCOUNT_ID:instance/INSTANCE_ID"
    }
  ]
}
```

### 실행기 설정

1. 기존에 수동으로 켜둔 본인 Agent·GUI는 종료합니다.
2. `Launch CloudNet.cmd`를 더블클릭합니다. 최초에는 boto3 등 필요한 모듈을 설치합니다.
3. EC2 콘솔에서 현재 인스턴스 ID(`i-...`)를 복사해 입력합니다. IP 주소와 다릅니다.
4. 리전은 현재 시드니 `ap-southeast-2`, 프로필은 위에서 준비한 `cloudnet`을 입력합니다.
5. SSH 키는 `C:\Users\user\.ssh\sheep_jae0_key.pem`을 사용합니다.
6. 시작 버튼을 누릅니다. 다음 실행부터 저장된 설정으로 자동 시작합니다.

기존 EC2의 `/home/ubuntu/cloudnet-direct` 코드·가상환경·인증정보는 그대로 사용합니다.
서버 재설치나 `server.provision` 재실행은 필요하지 않습니다.
같은 폴더의 수동 서버가 포트 5000을 사용 중이면 사용자·실행 명령·작업 폴더를 확인한 후에만
해당 서버를 서비스 실행으로 전환합니다. 다른 프로그램의 포트는 강제로 빼앗지 않습니다.
Ubuntu 기본 사용자에서 비밀번호 없는 `sudo`가 가능해야 합니다.

설정은 `runtime/launcher.json`에 저장됩니다. SSH 경로·서버 폴더·인증서 이름 등 고급 설정도 여기서 수정합니다.
두 실행기가 동시에 같은 로컬 프로젝트를 제어하지 못하도록 파일 잠금을 사용합니다.

## IP가 변경될 때

EC2를 중지/시작하면 자동 할당된 공인 IP가 바뀔 수 있습니다.
실행기는 새 IP를 AWS에서 조회하고 `runtime/current-endpoint.json`에 기록합니다.
본인 GUI와 Agent에는 새 접속 IP와 기존 인증서의 검증 이름을 각각 전달하므로 인증서 검증을 끄지 않습니다.
SSH도 기존 `32.236.13.91`로 저장해 신뢰한 호스트 키를 계속 검증합니다.

**기존 팀원 Agent에 입력된 IP는 원격으로 자동 변경되지 않습니다.**
주소가 바뀌면 최신 `agent/`와 `common/` 코드가 들어 있는 팀원 패키지에서 다음처럼 접속합니다.
토큰과 인증서는 기존 것을 유지합니다.

```powershell
& '.\.venv\Scripts\python.exe' -m agent.main --host NEW_PUBLIC_IP --server-name 32.236.13.91 --port 5000 --ca-file server.crt --token-file secrets/token.txt --pc-id PC-02 --live
```

PC-03은 해당 ID로 바꿉니다. 기존 인증서의 이름은 `32.236.13.91`이므로 `--server-name`은 그대로 유지합니다.
주소 재전달 없이 팀원까지 계속 자동 연결하려면 고정 DNS 또는 Elastic IP 같은 고정 접속 주소를 별도로 준비해야 합니다.
실행기는 추가 과금 자원을 임의 생성하거나 Elastic IP를 할당하지 않습니다.
각 PC 공인 IP가 바뀌었다면 보안 그룹의 해당 `/32` 규칙도 갱신해야 합니다.

## 종료 실패 및 복구

- 정상 GUI 종료 시에는 EC2 중지 확인까지 실행기 창을 유지합니다.
- 종료하면 팀원 Agent의 연결도 끊기고 재접속 대기 상태가 됩니다.
- 작업 관리자 강제 종료, PC 전원 끄기, 네트워크 단절 시 자동 중지를 보장할 수 없습니다.
- 정리 상태는 `runtime/launcher-session.json`에 기록됩니다. 다음 실행에서
  `이전 실행의 EC2 중지 / 중지 재시도` 버튼으로 기록된 인스턴스의 중지를 다시 시도할 수 있습니다.
- 중지 권한 거절이나 통신 실패가 표시되면 AWS 콘솔에서도 인스턴스 상태를 확인합니다.
- EC2가 중지돼도 EBS·유지한 Elastic IP 등 별도 자원에는 비용이 남을 수 있습니다.
- 오류 로그는 `runtime/launcher-clients.log`, Agent 로그는 `runtime/logs/agent.log`입니다.

## 검증 범위

자동화 테스트는 AWS 호출을 대체해 시작·중지 순서, 취소 중 정리, SSH 실패 시 EC2 중지,
중지 실패 기록, 인스턴스 삭제 API 미사용을 검증합니다. 실제 AWS 계정 제어는 별도 확인이 필요합니다.
테스트에서는 실제 인스턴스를 시작하거나 중지하지 않습니다.

참고: [AWS 인증 프로필](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html),
[EC2 시작·중지 시 주소 변경](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html)
