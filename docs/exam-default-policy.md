# 크롬 시험용 기본 감지 정책

시험모드는 실행을 차단하거나 프로세스를 종료하지 않고 감지 이벤트를 보냅니다.
Chrome(`chrome.exe`)은 시험 응시용으로 기본 목록에서 제외합니다.

## 종류별 감지 조건 (B 후속 변경)

- 아래 메신저·다른 브라우저·AI 앱의 알려진 이름은 표시된 최상위 창이 있을 때만 감지합니다.
  트레이/숨김·최소화·DWM cloaked 창은 제외합니다. 다른 창에 가려져 있어도
  Windows 표시 상태가 유지된 창은 포함합니다. 실제 사용 여부는 전경 전환 이력과 별개입니다.
- 원격제어 목록은 창 없이 실행 중이어도 감지합니다.
- 직접 지정한 목록에도 알려진 이름별 조건이 적용됩니다. 목록 지정이 기본 목록을 대체하는 규칙은 같습니다.
  분류되지 않은 사용자 지정 실행 파일은 종전처럼 모든 실행 인스턴스를 감지합니다.
- 일반 앱을 숨김/최소화했다가 다시 표시하면 같은 PID라도 재감지합니다.
  한 번의 표시 상태가 유지되는 동안 중복 알림은 보내지 않습니다.
- 창 조회 실패/비Windows에서는 일반 앱 감지를 추측하지 않고 MONITOR_STATUS=partial을 보냅니다.
  직전 감지 중복 상태는 보존하며 원격제어·미분류 프로그램 검사는 계속합니다.
- Windows 사용자 데스크톱에서 실행해야 합니다. 별도 세션·서비스와 일부 패키지 앱의
  대리 창 프로세스까지 완전한 연관성을 보장하지 않습니다.

### A·C 인계

이 변경은 B의 감지 조건만 바꾸며 이벤트 필드·종류·명령 규격은 추가하지 않습니다.
A 서버 변경 요청은 없습니다. C는 안내를 “일반 앱은 표시 창 기준, 원격제어는 백그라운드 포함”으로
맞추면 됩니다. 이력 자체에 창 상태를 나타내는 새 필드는 없습니다.
창 조회 실패의 partial과 전경 감시 unavailable은 서로 다른 상태입니다.
실제 Windows 최소화/트레이/복원 공동 검증은 남아 있으며 기존 감지 이력은 삭제하지 않습니다.
운영 Agent는 자동 재시작하지 않았습니다. 소유자와 검증 시각을 맞춰 재시작 후 새 시험에서 확인합니다.

API 기준: [IsWindowVisible](https://learn.microsoft.com/en-us/windows/desktop/api/winuser/nf-winuser-iswindowvisible),
[DWMWINDOWATTRIBUTE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute).

## 기본 대상

- 메신저: discord.exe, discordcanary.exe, discordptb.exe, kakaotalk.exe,
  telegram.exe, slack.exe, ms-teams.exe, teams.exe, whatsapp.exe, line.exe, skype.exe
- 원격제어: anydesk.exe, teamviewer.exe, teamviewer_service.exe, rustdesk.exe,
  mstsc.exe, vncviewer.exe, winvnc.exe
- 다른 브라우저: msedge.exe, firefox.exe, brave.exe, opera.exe, vivaldi.exe
- AI 데스크톱 앱: chatgpt.exe, claude.exe

프로세스 이름이 대소문자 구분 없이 정확히 일치할 때만 감지합니다.
목록은 시험 운영용 초기 정책이며 모든 제품·버전의 탐지를 보장하지 않습니다.
Chrome 내부의 메신저·AI 사이트, 확장 프로그램, 원격 접속 여부는 이 정책으로 감지하지 않습니다.
Edge 등을 백그라운드로 실행해도 감지될 수 있으므로 시험 전에 정책을 안내합니다.

## 적용 방법

로컬 Agent는 `--exam-mode --exam-id EXAM_ID`로 실행하면 기본 목록을 사용합니다.
`--forbid-process NAME.exe`를 지정하면 지정한 이름들로 기본 목록을 대체합니다.

원격 `SET_EXAM_MODE` 켜기 요청은 `forbidden_processes` 필드를 생략하면 기본 목록을
사용합니다. 명시한 1~100개 목록은 기본 목록을 대체합니다. 빈 목록 또는 null은 오류입니다.
응답과 `GET_EXAM_STATUS`의 `exam_mode.forbidden_processes`로 실제 적용 목록을 확인합니다.
기존 인계 문서의 목록 필수 조건은 이 동작으로 갱신합니다.

이 변경은 소스에 적용되며 이미 실행 중인 Agent의 정책은 자동으로 바뀌지 않습니다.
새 버전 Agent로 재시작하고 시험모드를 다시 켜야 합니다.
