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

시험을 끄거나 Agent 연결이 끊기거나 시험 탭을 닫으면 카메라 스트림을 종료합니다. 카메라 창을 닫아도 카메라가 꺼집니다. 마이크는 요청하지 않으며 영상은 이 PC의 미리보기에서만 사용합니다. Agent의 로컬 로그에는 `Chrome exam camera on`, `permission_denied`, `unavailable`, `off` 등의 상태만 기록합니다. 시험 또는 연결이 종료되는 순간에는 브라우저 카메라 표시가 사라지는지 확인합니다. [Chrome Windows API](https://developer.chrome.com/docs/extensions/reference/api/windows)

이미 확장 프로그램을 불러온 PC는 `chrome://extensions`에서 **CloudNet Control 시험 탭** 카드의 새로고침 버튼을 누릅니다. 같은 폴더와 확장 ID를 계속 쓰면 Windows 연결 등록은 그대로 사용할 수 있습니다. Agent가 실행 중이었다면 시험 전 기존 프로세스를 종료하고 최신 코드로 한 번 다시 실행합니다. PC-02는 `Start PC-02 Exam Control.cmd`를 사용합니다.

## 로컬 검증

- `시험 탭 → 탭 B → 탭 C → 시험 탭` 순서로 이동합니다. Agent 로그에서 `Chrome exam tab left` 1건과 `Chrome exam tab returned` 1건을 봅니다. 지정한 시험 탭을 닫으면 `Chrome exam tab closed`를 확인합니다.
- 시험 모드를 끄거나 Agent를 끊으면 감시가 해제됩니다. 확장 연결이 끊기면 Agent가 `Chrome tab monitor unavailable`을 기록합니다. 호스트가 비정상 종료되면 연결 상태가 만료되기까지 최대 8초가 걸릴 수 있습니다. 시험이 재시작되면 탭을 다시 지정합니다.
- 카메라 확인은 시험 전 `WAIT` 상태에서 미리보기가 뜨는지, 시험을 켰을 때 권한 허용 후 영상이 이 PC에서 보이는지, 시험을 껐을 때 카메라 창과 Chrome의 카메라 사용 표시가 사라지는지 확인합니다. `WAIT` 중에는 카메라 창이 열리지 않아야 합니다.
- 자동 검증은 `python -m unittest tests.test_chrome_tabs` 및 `node --test tests/test_chrome_extension.cjs tests/test_chrome_camera.cjs`입니다.

Agent는 현재 시험의 이벤트를 로컬 로그에 남깁니다. A 서버·공통 프로토콜·C GUI로 탭 전환을 보내려면 후속 연동이 필요합니다.
