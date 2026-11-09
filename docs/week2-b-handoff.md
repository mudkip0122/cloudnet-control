# B 담당 — 2주차 구현 결과와 A·C 인계

기준일: 2026-11-09. B의 책임은 Windows Agent의 등록·TLS 인증·수집·송수신·재접속입니다. EC2 서버·관리자 인증·오프라인 판정은 A, 관리자 목록 표시는 C의 책임입니다.

## 구현 완료

- PC ID 생성·파일 보존, 명시적 ID 지정, 중복 ID 등 등록 거절 시 종료 코드 2로 중단.
- 공통 `common.protocol` 사용: UTF-8 JSON, 4바이트 big-endian 길이 헤더, 최대 65,536바이트.
- 외부 연결은 TLS와 토큰 필수. CA·서버 이름·유효기간 검증, TLS 1.2 이상, PC별 토큰 등록.
- 인증서·TLS 규격·인증 오류와 일시적인 연결 장애 구분. 인증 실패는 중단하고 네트워크 장애는 재접속.
- 3·6·12·24·30초 백오프, 안정 세션(10초 이상) 후 초기화, 같은 ID로 TLS 재연결·재등록.
- 전용 수신 스레드와 선택적 Heartbeat 송신 스레드. 5초 Heartbeat를 측정 수집과 독립적으로 전송 가능.
- 연결별 송신 잠금으로 METRICS와 HEARTBEAT 프레임 보호. 종료 시 소켓 shutdown·작업 스레드 join 후 재접속.
- 연결 중단 시 측정값을 누적하지 않고 새 연결에서 새 네트워크 기준값으로 수집 재개.
- 콘솔·순환 파일 로그, 토큰과 원시 서버 응답은 로그에서 제외.

## A에게 전달할 통신 계약

1. Agent가 TLS 검증 후 `REGISTER`를 전송합니다. 필드는 `role: agent`, `pc_id`, `pc_name`, `token`입니다.
2. 성공 응답은 `REGISTER_RESULT`, `ok: true`, 요청과 같은 `pc_id`를 포함해야 합니다.
3. Heartbeat 수신을 지원하면 성공 응답에 `heartbeat_interval: 5`를 추가합니다. 없는 경우 기본 Agent는 METRICS만 전송합니다.
4. HEARTBEAT 필드는 `type`, 등록된 `pc_id`, UTC ISO 8601 `timestamp`입니다. METRICS는 2초 간격입니다.
5. ACK는 생략하거나 `{"type":"HEARTBEAT_ACK","pc_id":"PC-02"}`로 응답합니다. 이후 확장으로 프로세스 조회·종료 COMMAND도 처리합니다. [별도 명령 계약](process-control.md)에 따르며 기본 비활성입니다. 그 밖의 미지원 메시지는 세션 오류로 처리합니다.
6. 서버에서 인증 세션의 ID 일치, 중복 접속 거절, 최근 생존 신호 기준 15초 타임아웃을 처리합니다. 타임아웃은 서버 책임이며 B 구현으로 대체되지 않습니다.
7. 실패 시 ERROR 또는 실패 REGISTER_RESULT 후 연결을 닫습니다. Agent는 등록·프로토콜 거절 후 무한 재접속하지 않습니다.

정확한 예시는 [통신 규격](protocol.md)을 참조합니다. 서버에서 `heartbeat_interval`을 보내지 않는 경우 A가 수신 지원을 확인한 뒤 Agent 명령에 `--heartbeat-interval 5`를 추가할 수 있습니다. 미지원 서버에는 추가하지 않습니다.

## C에게 전달할 사항

- Agent는 GUI와 직접 통신하지 않습니다. 서버의 목록·상태 조회를 사용합니다.
- PC ID가 식별 기준입니다. 같은 PC는 재접속해도 같은 ID이며 각 PC의 토큰과 ID가 일치해야 합니다.
- CPU·RAM은 %, 송수신은 Mbps입니다. 첫 네트워크 샘플·수집 실패는 null입니다.
- 연결 종료 후 오프라인 표시와 오래된 값 숨김은 서버 상태·측정 시각을 기준으로 처리합니다.

## 1주차 등록 정보 정의와 제어 범위

- PC 이름은 `socket.gethostname()`을 기본값으로 사용하며 `--pc-name`으로 변경합니다. PC ID와 이름은 별개입니다.
- OS 정보가 필요할 때는 `platform.system()`, `platform.release()`, `platform.version()`으로 수집합니다.
- 부팅 시각은 `psutil.boot_time()`의 Unix 초를 UTC로 변환해 사용합니다. 같은 PC의 같은 부팅은 같은 부팅 시각을 기준으로 식별하고, 이후 재부팅 검증에서는 PC ID와 이전·이후 부팅 시각을 함께 비교합니다. 시계 변경·정밀도 영향도 재부팅 기능 구현 시 검증해야 합니다.
- OS·부팅 정보는 수집 방법만 정의했으며 현재 REGISTER에 추가하지 않습니다. A와 필드명을 합의한 뒤 원격 제어 주차에 공통 규격과 함께 구현합니다.
- 현재 Agent에는 Windows 종료·재부팅 실행 코드가 없습니다. 프로세스 COMMAND는 별도 활성화 시 처리하며, 비활성 상태는 PROCESS_CONTROL_DISABLED, 미지원 action은 UNSUPPORTED_ACTION으로 응답합니다.
- 실제 테스트 PC와 소유자 동의는 각 PC 소유자가 공동 검증 전에 확인해야 합니다. 이후 원격 제어도 별도 명시적 활성화와 승인된 PC 범위에서 구현합니다.

## 재현 명령

저장소 루트에서 실행합니다. 테스트는 임시 인증서·테스트 토큰과 루프백 서버를 사용하며 배포 인증정보를 읽지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_tls
.\.venv\Scripts\python.exe -m tests.smoke_reconnect
.\.venv\Scripts\python.exe -m tests.smoke_agents
```

Windows 통합 테스트는 자신이 시작한 프로세스 트리를 taskkill로 정리합니다. 임의의 사용자 Agent나 서버는 종료하지 않습니다. 제한된 실행 환경에서는 테스트 프로세스 정리 권한이 필요할 수 있습니다.

## 실제 PC 3대·EC2에서 남은 공동 검증

- [ ] A가 현재 EC2 주소·5000 포트 리스닝·보안 그룹·서버 실행 상태를 확인.
- [ ] A가 HEARTBEAT 수신과 `heartbeat_interval: 5` 응답 또는 CLI 활성화에 합의.
- [ ] 각 PC에 같은 저장소와 의존성을 설치하고 해당 PC의 ID·토큰·인증서를 설정.
- [ ] 실제 PC 3대 동시 접속 후 A 서버·C 목록에서 서로 다른 ID와 온라인 상태 확인.
- [ ] 한 PC 네트워크 단절 후 15초 기준 오프라인 판정 및 다른 PC 유지 확인.
- [ ] 네트워크 복구 후 같은 프로세스·ID로 재등록 및 최신 값 재개 확인.
- [ ] A가 EC2 서버를 재시작한 후 세 Agent의 자동 복구 확인.
- [ ] 배포 환경의 중복 ID·잘못된 토큰 거절 확인. 정상 PC의 세션은 유지되어야 함.
- [ ] 결과·시각·화면·로그를 팀 테스트 결과표에 기록.

B의 PC 실행 명령(기존에 받은 PC-02 토큰 사용):

```powershell
.\.venv\Scripts\python.exe -m agent.main --host 32.236.13.91 --port 5000 --ca-file secrets/server.crt --token-file secrets/agent-token.txt --pc-id PC-02 --live
```

위 IP는 기존 배포 정보이며 현재 유효한 주소인지는 A가 확인해야 합니다. 다른 PC에는 해당 PC의 ID와 토큰을 사용합니다. 인증정보·runtime 폴더를 다른 PC에 그대로 복사하지 않습니다.

## 현재 EC2 확인 결과

최신 확인: 2026-11-09 14:20 KST, 새 주소 `3.26.40.204:5000`에서는 TCP 연결이 성립했으나 TLS 검증 결과 `IP address mismatch`가 확인됐습니다. 기존 인증서는 새 IP에 유효하지 않아 PC-02 등록·토큰 인증·측정 전송은 수행하지 못했습니다. A가 고정 주소를 확정하고 일치하는 인증서를 적용·배포하면 B가 재검증합니다. Elastic IP 적용은 아직 확인하지 않았습니다.

재확인: 2026-11-09 14:18 KST, 서버가 켜졌다는 안내 후 같은 주소·PC-02 설정으로 등록 전용 실행을 다시 수행했으나 TCP 연결이 약 5초 뒤 TimeoutError로 끝났습니다(종료 코드 1). TLS·토큰 검증과 측정값 전송은 시작되지 않았습니다. 재확인 로그: `runtime/logs/ec2-recheck.log`. A가 현재 공인 IP, 5000 포트 리슨 주소, 보안 그룹의 접속 출발지 허용 여부를 확인해야 합니다.

2026-11-09 14:00 KST, 기존 주소 `32.236.13.91:5000`에 PC-02의 등록 전용 명령을 실행했습니다. TCP 연결 단계에서 약 5초 뒤 TimeoutError, 종료 코드 1로 끝났습니다. TLS·토큰 인증 단계에는 도달하지 않았습니다. 이 결과만으로 서버 중지·보안 그룹·주소 변경 중 어느 원인인지 단정하지 않습니다. 로그는 Git 제외 경로 `runtime/logs/week2-ec2-check.log`에 있습니다.

실제 EC2 서버 재시작·보안 그룹 수정·다른 PC 조작은 수행하지 않았습니다. 위 공동 검증을 통과하기 전까지 프로젝트 전체 2주차 완료로 표시하지 않습니다.
