# A 담당 서버 구현·검증·운영 상태

갱신일: 2026-11-09. A는 서버·클라우드·공통 규격·A 문서만 담당합니다. B·C 코드는 임의로 수정하지 않습니다.

## 구현 완료

- TLS 1.2 이상·PC별 Agent 토큰·관리자 전용 토큰 검증.
- UTF-8 JSON·4바이트 big-endian 길이 헤더·최대 65,536바이트.
- 미등록 요청·역할 위반·잘못된 JSON·과대 프레임·중복 PC ID 거절.
- 연결별 스레드·공유 세션 잠금·전체 프레임 송신 잠금.
- METRICS·HEARTBEAT 생존 확인·기본 15초 만료·해당 PC만 오프라인. 불완전 프레임으로 만료 연장 불가.
- 관리자 DEVICE_LIST·선택적 STATUS_UPDATE와 제한된 알림 큐.
- LIST_PROCESSES·STOP_PROCESS와 결과 중계. 명령 ID·만료·대상 TLS 세션·중복 검사.
- 등록·해제·만료·오류 로그. 토큰·원시 수신 본문은 로그에 출력하지 않음.

SQLite 이력·경고·PC 종료/재부팅·영구 명령 감사 이력은 후속 주차입니다.

## 배포·실제 검증

main 575e950의 server/·common/을 2026-11-09 06:19 UTC에 EC2에 배포했습니다. Agent·GUI·인증 파일 해시는 보존됐습니다. 백업은 /home/ubuntu/cloudnet-backups/before-relay-20261109T061916Z.tar.gz입니다.

| 검증 | 결과 |
|---|---|
| 실제 PC-01·02·03 동시 연결·측정 수신 | 확인 완료 |
| 서버 교체 후 세 Agent 자동 재접속 | 확인 완료 |
| PC-01 종료 시 해당 PC만 오프라인 | 확인 완료 |
| PC-02·03 온라인·새 측정값 유지 | 확인 완료 |
| PC-01 동일 ID·기존 인증정보 재접속 | 확인 완료 |
| 관리자 GUI 수신 작업의 인증·세 PC 갱신 | 확인 완료, 화면 육안 검증과 구분 |
| EC2 별도 TLS 서버의 세션·토큰·중복 ID·Heartbeat 만료 | 통과, 기존 인증정보 재사용 |
| EC2 별도 서버 + B 명령 처리 코드의 목록·검증용 자식 종료·결과 중계 | 통과, 운영 Windows 앱 종료 시연과 구분 |
| 운영 PC-01 목록 | 활성화 후 283개, 후속 288개 수신 |
| 운영 PC-02 목록 | 후속 점검 393개 수신 |
| 운영 PC-03 목록 | 후속 점검 302개 수신 |

과거 검증이 현재 PC가 항상 켜져 있음을 뜻하지 않습니다. 최신 서버 확인에서 서비스는 AWS 설정 작업 전인 07:50:31 UTC에 정상 중지됐고 현재 리스너는 없습니다. 임의로 재시작하지 않았습니다.

최근 관련 테스트 22개 통과:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_protocol tests.test_monitoring tests.test_server_week2 tests.test_process_relay tests.test_heartbeat -v
```

## B·C 인계

B main 53f2bbf부터 --server-name을 공식 지원합니다. Heartbeat 기본값 0, --heartbeat-interval 5로 활성화할 수 있습니다. 서버는 ACK를 보내지 않고 METRICS도 생존 신호로 인정합니다.

C GUI는 관리자 토큰과 --host 13.238.97.144 --server-name 32.236.13.91을 사용합니다. 대상 Agent에서 --enable-process-control이 필요합니다. STATUS_UPDATE는 REGISTER의 subscribe_status=true로 선택 구독합니다.

## 1주차 마감 상태

보안 그룹 실제 조회 완료: SSH 관리자 /32, 5000번 관리자·팀원 /32 정상. 월 USD 50 예산과 이메일 알림 3개·수신자 등록도 API로 확인했습니다. 실제 이메일 수신은 사용자 확인 대기입니다. AWS CLI 인증·스킬·MCP 연결 검증은 완료됐습니다.

[실행·종료](ec2-direct.md), [AWS 설정·비용·자원 정리](week1-operations.md)를 따릅니다. 이전 배포·원복 기록은 Git 커밋 33307f6, 3661161, 575e950, 6f23f92에 보존돼 있습니다.
