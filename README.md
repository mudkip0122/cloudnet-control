# CloudNet Control

AWS EC2 기반 TCP/TLS 통신으로 Windows PC의 상태를 모니터링하고, 인증된 관리자 명령으로 테스트 PC를 종료·재부팅하는 3인 팀 프로젝트입니다.

현재는 프로젝트 구조를 준비하는 단계이며, 실행 가능한 서버·Agent·GUI는 아직 구현되지 않았습니다.

## 목표

- PC 3대의 CPU·RAM·네트워크 사용량 실시간 모니터링
- TLS 통신, Agent/Admin 인증 및 Heartbeat 기반 접속 상태 관리
- SQLite 이력 저장 및 관리자 GUI 표시
- 승인된 테스트 PC의 종료·재부팅 및 명령 감사 로그

## 프로젝트 구조

```text
server/       서버, 인증, 세션 관리, SQLite 저장
agent/        Windows 상태 수집, 재연결, 허용 명령 실행
admin_gui/    관리자 화면, 그래프, 제어 결과 표시
common/       공통 메시지 규격과 통신 코드
docs/         개발 계획, 프로토콜, 설치·실행 문서
tests/        통신, 인증, 통합 테스트
```

## 기술

Python 3, socket, ssl, threading, psutil, CustomTkinter, Matplotlib, SQLite, AWS EC2 Ubuntu

## 개발 환경 준비

Python 세부 버전과 의존성 버전은 팀에서 확정 후 기록합니다. 현재 설치할 프로젝트 의존성은 없습니다.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

`.env`는 설정 항목을 공유하기 위한 예시입니다. 자동 로딩 기능은 아직 구현되지 않았습니다.
서버 주소, 인증정보, TLS 파일은 실제 구현과 배포 환경에 맞춰 설정합니다.

## 팀 역할

| 역할 | 주 책임 | 담당자 |
|---|---|---|
| A | 서버·클라우드·인증·배포 | 미정 |
| B | Agent·수집·재연결·명령 실행 | 미정 |
| C | GUI·그래프·이력 조회 | 미정 |

## GitHub 협업

- 저장소 기본 브랜치는 `main`입니다.
- 기능별 브랜치에서 작업하고 Pull Request로 병합합니다.
- 예: `feat/server-connect`, `feat/agent-metrics`, `feat/gui-device-list`
- 작업은 Issue에 담당자와 완료 조건을 기록합니다.
- PR에는 변경 목적, 확인 방법과 결과를 적고 다른 팀원 1명이 검토합니다.
- 비밀번호, 토큰, AWS 자격증명, 인증서 개인키는 커밋하지 않습니다.

## 첫 통합 목표

Agent가 전송한 CPU·RAM 값이 서버를 거쳐 관리자 GUI에 표시되는 흐름을 완성합니다.
메시지 규격을 먼저 합의하고 로컬 연결을 검증한 뒤 TLS·인증을 적용해 EC2 연결을 확인합니다.

## 문서

- [6주 프로젝트 계획서](docs/project-plan.md)
- [A 담당: 서버·클라우드](docs/roles/A-server-cloud.md)
- [B 담당: Agent·원격 제어](docs/roles/B-agent-control.md)
- [C 담당: 관리자 GUI·데이터](docs/roles/C-gui-data.md)
