# B 담당 Chrome 시험 탭 감지

시험 참가자가 지정한 탭에서 같은 Chrome 창의 다른 탭으로 전환하면 Agent가 `left`, 지정 탭으로 돌아오면 `returned`를 로컬 로그에 남깁니다. 다른 탭끼리 이동하면 이탈을 반복 기록하지 않습니다. 시험 탭이 닫히면 `closed`를 기록합니다. 지정 탭을 다른 Chrome 창으로 옮기면 감시를 해제하며, 새 창에서 다시 지정할 수 있습니다.

확장 프로그램은 `chrome.tabs.onActivated`로 받은 탭 ID와 창 ID를 메모리에서 비교합니다. Native Messaging으로 연결 상태와 전환 종류를 전달합니다. 탭 ID·창 ID·URL·제목·페이지 내용은 Agent에 전달하지 않습니다. 같은 탭에서 주소를 바꾸거나 다른 Chrome 창에 포커스하는 동작은 탭 감지 범위에 포함하지 않습니다. 다른 Windows 앱으로 이동하는 동작은 기존 전경 감지 범위입니다. [Chrome Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs) · [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)

## Windows 시험 PC 설치

1. 해당 Windows 사용자 프로필의 Chrome에서 `chrome://extensions`를 열고 개발자 모드를 켭니다. **압축해제된 확장 프로그램을 로드합니다**로 저장소의 `chrome_extension` 폴더를 선택합니다.
2. Chrome에 표시된 확장 프로그램 ID를 복사합니다. 같은 Windows 사용자 계정의 PowerShell에서 저장소 루트로 이동해 아래 명령을 실행합니다. 명령은 ID 하나를 허용하는 호스트 매니페스트를 `runtime/`에 쓰고 현재 사용자 레지스트리에 이 프로젝트의 호스트를 등록합니다.

   ```powershell
   .\.venv\Scripts\python.exe -m agent.chrome_native_host --install '복사한_확장_프로그램_ID'
   ```

3. 기존 시험용 Agent의 실행 명령에 `--watch-chrome-tabs`를 추가합니다. 원격 시험에서는 기존 `--allow-remote-exam-control`도 유지하고 관리자 화면에서 시험을 시작합니다. 로컬 시작을 사용하는 경우 기존 `--exam-mode`와 `--exam-id`를 유지합니다. TLS와 PC별 ID·인증정보는 기존 실행과 같습니다. 프로젝트 폴더를 옮기거나 확장 프로그램 ID가 바뀌면 새 위치 및 ID로 2단계를 반복합니다.
4. 시험 참가자가 시험 탭을 활성화하고 확장 아이콘을 한 번 누릅니다. `WAIT`가 표시되면 최대 10분 동안 시험 시작을 기다립니다. `ON`은 감시 시작, `AWAY`는 지정 창에서 다른 탭을 사용 중임을 표시합니다. 같은 아이콘을 다시 누르면 감시가 해제됩니다.

## 시험 카메라

확장 프로그램 0.2.0부터는 참가자가 탭을 지정한 상태에서 시험 모드가 켜지면 작은 Chrome 창이 열리고 카메라 권한을 확인합니다. Chrome이 요청하면 참가자가 **허용**을 선택합니다. 카메라를 사용할 수 있으면 해당 창에 로컬 미리보기가 표시됩니다. 이미 권한을 허용한 경우 시험 시작 후 자동으로 미리보기가 켜집니다. 확장 아이콘에 마우스를 올리면 카메라 상태를 확인할 수 있습니다.

시험을 끄거나 Agent 연결이 끊기거나 시험 탭을 닫으면 카메라 스트림을 종료합니다. 카메라 창을 닫아도 카메라가 꺼집니다. 마이크는 요청하지 않으며 영상 공유가 비활성화된 경우에는 PC의 미리보기에만 표시됩니다. Agent의 로컬 로그에는 `Chrome exam camera on`, `permission_denied`, `unavailable`, `off` 등의 상태만 기록합니다. 시험 또는 연결이 종료되는 순간에는 브라우저 카메라 표시가 사라지는지 확인합니다. [Chrome Windows API](https://developer.chrome.com/docs/extensions/reference/api/windows)

이미 확장 프로그램을 불러온 PC는 `chrome://extensions`에서 **CloudNet Control 시험 탭** 카드의 새로고침 버튼을 누릅니다. 같은 폴더와 확장 ID를 계속 쓰면 Windows 연결 등록은 그대로 사용할 수 있습니다. Agent가 실행 중이었다면 시험 전 기존 프로세스를 종료하고 최신 코드로 한 번 다시 실행합니다. PC-02는 `Start PC-02 Exam Control.cmd`를 사용합니다.

## 로컬 검증

- `시험 탭 → 탭 B → 탭 C → 시험 탭` 순서로 이동합니다. Agent 로그에서 `Chrome exam tab left` 1건과 `Chrome exam tab returned` 1건을 봅니다. 지정한 시험 탭을 닫으면 `Chrome exam tab closed`를 확인합니다.
- 시험 모드를 끄거나 Agent를 끊으면 감시가 해제됩니다. 확장 연결이 끊기면 Agent가 `Chrome tab monitor unavailable`을 기록합니다. 호스트가 비정상 종료되면 연결 상태가 만료되기까지 최대 8초가 걸릴 수 있습니다. 시험이 재시작되면 탭을 다시 지정합니다.
- 카메라 확인은 시험 전 `WAIT` 상태에서 미리보기가 뜨는지, 시험을 켰을 때 권한 허용 후 영상이 이 PC에서 보이는지, 시험을 껐을 때 카메라 창과 Chrome의 카메라 사용 표시가 사라지는지 확인합니다. `WAIT` 중에는 카메라 창이 열리지 않아야 합니다.
- 자동 검증은 `python -m unittest tests.test_chrome_tabs` 및 `node --test tests/test_chrome_extension.cjs tests/test_chrome_camera.cjs`입니다.

## 시험 타임라인 연동

Agent는 시험 이벤트를 로컬 로그에 남깁니다. 새 이벤트를 수신할 준비가 된 A 서버는 인증된 TLS 연결의 `REGISTER_RESULT`에 `exam_events_v1: true`와 `exam_chrome_timeline_v1: true`를 모두 보냅니다. B는 두 플래그를 연결마다 다시 확인하고, `--watch-chrome-tabs`가 활성화된 시험에서만 전송합니다. 기존 서버에서는 로컬 로그를 계속 사용합니다.

세 종류 모두 `EXAM_EVENT` 버전 `1`이며, 공통 필드는 `type`, `version`, `event_id`, `pc_id`, `exam_id`, `kind`, UTC `timestamp`입니다. 탭·카메라·확장 연결 전환에는 Agent가 읽어 들인 시각 대신 로컬 브리지에 기록된 발생 시각을 사용합니다.

| `kind` | 추가 필드 | 값 |
|---|---|---|
| `EXAM_TAB_CHANGED` | `transition` | `left`, `returned`, `closed` |
| `EXAM_CAMERA_STATUS` | `status` | `on`, `off`, `permission_denied`, `unavailable` |
| `EXAM_TAB_MONITOR_STATUS` | `status`, `reason`, 조건부 `dropped_count` | `monitoring`은 `extension_connected`. `unavailable`은 `awaiting_extension`, `extension_disconnected`, `local_store_unavailable`, `event_buffer_overflow`. `dropped_count`는 `event_buffer_overflow`일 때만 포함하는 양의 정수입니다. |

```json
{"type":"EXAM_EVENT","version":1,"event_id":"b8cb9b27-44de-4919-b4ec-b0a5cd7df253","pc_id":"PC-02","exam_id":"trial-1","timestamp":"2026-11-18T12:30:00.250000Z","kind":"EXAM_TAB_CHANGED","transition":"left"}
```

- A는 세 종류를 검증하고 기존 `event_id` 중복 제거·SQLite 저장·관리자 구독 중계에 연결합니다. Agent 연결 단절·재접속은 A의 접속 상태 타임라인을 사용합니다. 같은 시험 ID를 다시 쓸 경우 시험 시작 기록과 Agent 접속 세션으로 관찰 구간을 구분합니다.
- C는 `EXAM_TAB_MONITOR_STATUS`의 `unavailable`을 확장 감시 공백으로 표시하고, 그 경계를 넘겨 탭 이탈 시간을 계산하지 않습니다. `event_buffer_overflow`는 직전 수집 구간에 일부 기록이 누락됐다는 뜻입니다. 확장 연결이 복구되더라도 새 카메라 상태가 오기 전까지는 카메라 상태를 미확인으로 표시합니다.
- `EXAM_CAMERA_STATUS`의 `off`는 확장에서 확인한 상태입니다. 시험 종료나 연결 해제 시점에는 마지막 `on`을 그대로 유지하지 않고 해당 시험의 관찰을 종료합니다. 영상·음성·URL·제목·탭 ID는 전달하지 않습니다.
- 연결이 끊겨 전송하지 못한 시험 이벤트는 다음 연결로 재전송하지 않습니다. 실서버 검증을 시작하기 전에 A의 새 이벤트 검증과 구독 중계가 준비되어야 합니다.

## 감독자 영상 송출 연동

확장 프로그램 0.3.0과 새 Agent를 사용합니다. PC-01·02·03은 각 PC의 실행 CMD를 더블클릭하면 Chrome 탭 감시와 영상 공유 옵션이 켜집니다. 직접 Agent를 실행할 경우 기존 인자에 `--watch-chrome-tabs`와 `--share-exam-camera`를 추가합니다. 참가자가 탭을 지정하고 시험을 시작하는 순서는 동일합니다. 카메라 창은 공유 여부를 표시하고 Chrome의 카메라 권한을 따릅니다.

Agent는 로컬 옵션이 켜져 있고 인증된 TLS 연결의 `REGISTER_RESULT`에 `exam_camera_stream_v1: true`가 있을 때만 활성 시험에서 영상을 보냅니다. 각 메시지는 기존 JSON 프레이밍을 쓰며 필드는 다음과 같습니다.

| 필드 | 값 |
|---|---|
| `type`, `version` | `EXAM_CAMERA_FRAME`, `1` |
| `pc_id`, `exam_id` | 인증된 Agent의 PC ID와 현재 시험 ID |
| `timestamp` | Agent가 프레임을 전달하는 시각, UTC ISO 8601 |
| `format`, `data` | `jpeg`, 표준 Base64 문자열 |

영상은 최대 320×240, 초당 2프레임, 압축 프레임당 최대 24 KiB입니다. B는 프레임을 메모리로 전달하고 디스크와 로그에는 저장하지 않습니다. 연결이 끊겨 누락된 프레임은 재전송하지 않습니다.

A는 이 형식을 검증하고 해당 PC·시험을 볼 권한이 있는 관리자 연결에만 중계할 수 있을 때 Agent 등록 응답에 지원 플래그를 켭니다. 느린 뷰어에는 이전 프레임을 쌓지 않습니다. C는 선택한 PC와 현재 시험의 프레임을 표시하고, 카메라 꺼짐·시험 종료·단절 또는 3초간 새 프레임이 없으면 마지막 영상을 지웁니다. 운영 화면에서의 재생은 A·C 연동 후 공동 검증합니다.
