# 크롬 시험용 기본 감지 정책

시험모드는 실행을 차단하거나 프로세스를 종료하지 않고 감지 이벤트를 보냅니다.
Chrome(`chrome.exe`)은 시험 응시용으로 기본 목록에서 제외합니다.

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
