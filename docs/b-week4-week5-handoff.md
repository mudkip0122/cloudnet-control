# B 담당 4·5주차 검증 및 연동

## 4주차 장애 검증

Agent는 등록에 성공한 연결마다 새 측정 스트림을 시작합니다. 단절 중에는 전송 대기열이 생기지 않습니다. 재접속 때 PC ID는 유지되고 첫 CPU·RAM 값은 새로 수집합니다. 첫 송수신 속도는 차분 기준이 없어 `null`이며 두 번째 샘플부터 계산합니다. Heartbeat와 RTT 작업도 연결별로 시작하고 정리합니다.

| 시나리오 | B 조작 | A·C 확인 |
|---|---|---|
| CPU·RAM 부하 | 승인된 테스트 PC에 아래 15초 부하를 실행하고 PC ID·시작 시각을 기록 | 해당 PC의 측정값만 변화하는지, A의 경고 임계치와 지속 시간을 충족했을 때 C에 경고가 한 번 표시되는지 확인 |
| 네트워크 단절 | 테스트 PC의 서버 연결 경로만 Heartbeat 제한 시간보다 길게 끊고 복구 | 오프라인 전환·이전 측정값 숨김·동일 ID 재접속·신규 샘플 수신과 다른 PC 수신 유지 확인 |
| 서버 종료·재시작 | A가 테스트 서버를 재시작하는 동안 Agent는 실행 유지 | 자동 재인증, PC ID 한 개, 복구 후 현재 시각의 새 데이터 확인. 저장된 이력의 유지 판정은 A의 DB 기준 적용 |
| 인증·로그 | 로컬 TLS 검증에 합성 토큰 사용 | 잘못된 토큰으로 연결할 때 자동 재시도를 중단하고 로그에 토큰 값과 개인키 본문이 표시되지 않는지 확인 |

```powershell
@'
import time

payload = bytearray(128 * 1024 * 1024)
payload[::4096] = b'x' * (len(payload) // 4096)
end = time.monotonic() + 15
while time.monotonic() < end:
    pass
'@ | .\.venv\Scripts\python.exe -
```

이 부하는 CPU 코어 하나와 약 128 MiB를 사용합니다. 전체 CPU/RAM 90% 경고를 재현해야 할 경우 A가 테스트 환경에서 관측 가능한 임계치와 지속 시간을 선택합니다.

재현 명령은 `python -m tests.smoke_reconnect`와 `python -m tests.smoke_tls`입니다. 첫 검증은 로컬 서버 3회 재시작, 중복 등록 거절, 같은 실행 프로세스·ID의 실측 전송 복구를 검사합니다. 두 번째 검증은 TLS 연결 종료 후 재인증, 첫 샘플의 수집 시각과 네트워크 결측, 합성 인증정보의 로그 비노출을 검사합니다.

## 5주차 Agent 동작

- 기존 프로세스 조회·개별/일괄 종료와 Windows 보호 정책을 유지합니다. 프로세스 명령 ID는 연결이 바뀌어도 같은 Agent 실행 동안 60초간 보존하므로, Agent가 다시 시작된 뒤의 프로세스 명령 재전송은 A의 SQLite 감사 예약으로 막습니다. 현재 A 감사 DB의 보존 기간은 30일입니다.
- PC 전원 명령은 `SHUTDOWN_PC`, `REBOOT_PC`만 지원합니다. `--power-control simulate`는 접수와 모의 결과만 보내고 실기기에서 실행하려면 승인된 Windows 테스트 PC에서 `--power-control execute`를 명시합니다. 두 모드는 `--live`와 기존 TLS·PC별 토큰이 필요합니다. 기본값은 `off`입니다.
- 전원 명령 ID는 실행 전에 `agent-power.sqlite3`에 기록합니다. 기본 위치는 `runtime/`이며, `--id-file`을 지정했다면 그 파일의 상위 디렉터리입니다. 같은 PC에서 Agent 재시작 후에도 60초에 걸친 유효 명령 재생을 막으려면 같은 저장 경로를 유지합니다. 서버는 보존 중인 동일 ID에 새 만료 시각을 붙여 보내지 않습니다.
- `boot_id`는 Windows `Win32_OperatingSystem.LastBootUpTime`의 UTC 시각입니다. 실행 모드에서 이 값을 읽을 수 없으면 전원 제어를 비활성화합니다. 실제 실행은 Windows 시스템 디렉터리의 `shutdown.exe`에 고정된 `/s /t 0` 또는 `/r /t 0`만 전달합니다. [Windows 부팅 시각](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-operatingsystem) · [Windows shutdown](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown)

## A·C 연동 제안

등록 시 Agent는 확인 가능한 `boot_id`와 활성화한 `power_control_mode`(`simulate` 또는 `execute`)를 전송합니다. 모의 모드에서 부팅 시각을 읽을 수 없다면 `boot_id`는 `null`입니다. A 서버는 아래 규격을 지원할 때 `REGISTER_RESULT`에 `power_control_v1: true`를 보내고, PC별 상태에 등록 필드를 노출합니다.

`COMMAND`에는 기존 `command_id`, `target_id`, `action`, UTC `expires_at` 외에 `target_boot_id`, `power_control_mode`가 필요합니다. 부팅 ID와 모드는 대상의 현재 등록값을 사용합니다. A는 관리자 인증·대상 연결·전원 모드·부팅 ID를 확인하고 감사 DB에 요청을 먼저 예약합니다. C는 대상 PC·동작·모드를 확인창에 표시합니다.

| `COMMAND_RESULT` | `ok` | `terminal` | 처리 |
|---|---|---|---|
| `POWER_ACCEPTED` | `true` | `false` | Agent가 검증 및 ID 저장을 마쳤습니다. A는 요청을 계속 유지하고 접수 상태를 기록합니다. |
| `POWER_SIMULATED` | `true` | `true` | 모의 실행 확인 |
| `POWER_EXECUTION_STARTED` | `true` | `true` | Windows가 요청을 접수했습니다. 재부팅 완료 판정은 이후 등록 결과를 따릅니다. |
| `POWER_EXECUTION_UNKNOWN` | `false` | `true` | Windows 명령 응답 제한 시간 초과. 자동 재전송하지 않습니다. |
| `POWER_EXECUTION_FAILED` | `false` | `true` | Windows 명령 호출 또는 종료 코드 오류 |
| `POWER_CONTROL_DISABLED`, `POWER_UNAVAILABLE`, `POWER_MODE_MISMATCH`, `BOOT_ID_MISMATCH`, `POWER_STATE_UNAVAILABLE` | `false` | `true` | 설정, 부팅 세션 또는 로컬 저장소 확인 |
| `INVALID_COMMAND_ID`, `WRONG_TARGET`, `INVALID_EXPIRY`, `EXPIRED_OR_TOO_FAR`, `DUPLICATE_COMMAND`, `COMMAND_CAPACITY` | `false` | `true` | 공통 요청 검증 거절 |

A는 `POWER_ACCEPTED` 이후 같은 Agent 세션에서 최종 응답을 받을 시간을 추가로 확보합니다. 요청 만료는 동작을 시작할 수 있는 기한이며 Windows 호출은 최대 4초를 기다립니다. 연결이 끊겨 최종 응답이 없으면 결과를 미확인으로 보존합니다. 재부팅 시험에서는 동일 PC가 재등록한 뒤 이전과 서로 다른 유효한 `boot_id`를 확인합니다. 종료는 연결 단절과 테스트 PC의 실제 전원 상태를 함께 확인합니다. C는 모의·접수·실행 시작·확인 완료를 각 상태에 맞게 표시합니다.

## 검증 기록

- Agent 관련 단위 테스트 72개, 로컬 서버 3회 재시작, 로컬 TLS 모의 재부팅·재접속·중복 거절을 확인했습니다.
- 로컬 TLS에서 보호 대상 거절, 오래된 PID 정보 거절, 테스트가 만든 프로세스 종료를 확인했습니다.
- 동일 Windows 부팅 세션에서 부팅 ID를 두 번 읽어 일치함을 확인했습니다. 실제 PC 전원 제어와 A·C 연동은 후속 공동 검증 대상입니다.
- 전체 테스트 239개 중 A 서버의 `test_freshness_is_independent_of_heartbeat_and_agent_clock` 1개가 실패했습니다. 해당 테스트는 Agent를 호출하지 않으며, 단독 재실행에서도 메트릭과 직후 Heartbeat의 수신 시각이 같은 값으로 기록됐습니다.
