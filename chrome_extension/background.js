const nativeHost = "com.cloudnet_control.exam_tabs";
const cameraLabels = {
  starting: "카메라 권한 확인 중",
  on: "카메라 켜짐",
  permission_denied: "카메라 권한 거부",
  unavailable: "카메라 사용 불가",
  off: "카메라 꺼짐",
};
const replyTimeout = 7000;
const armingTimeout = 10 * 60 * 1000;
let watch = null;

function display(text, title) {
  void chrome.action.setBadgeText({ text });
  void chrome.action.setTitle({ title: `CloudNet Control · ${title}` });
}

function stop(current, text, title) {
  if (watch !== current) return;
  watch = null;
  clearInterval(current.timer);
  closeCamera(current);
  current.port.disconnect();
  display(text, title);
}

function closeCamera(current) {
  const tabId = current.cameraTabId;
  current.cameraPort?.disconnect();
  if (Number.isInteger(tabId)) void chrome.tabs.remove(tabId).catch(() => {});
}

function displayMonitoring(current) {
  const tabs = current.away ? "다른 탭 활성화" : "시험 탭 감시 중";
  display(current.away ? "AWAY" : "ON", `${tabs} · ${cameraLabels[current.cameraState]}`);
}

function reportCamera(current, state) {
  if (watch !== current || current.closing || current.cameraState === state) return;
  current.cameraState = state;
  displayMonitoring(current);
  send(current, { kind: "camera", state });
}

async function openCamera(current) {
  current.cameraUrl = chrome.runtime.getURL(`camera.html?session=${crypto.randomUUID()}`);
  try {
    // Use a regular Chrome window so its camera permission prompt is visible.
    await chrome.windows.create({ url: current.cameraUrl, type: "normal",
      width: 410, height: 460, focused: true });
  } catch {
    reportCamera(current, "unavailable");
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "cloudnet-exam-camera") return;
  const current = watch;
  if (!current?.trusted || current.closing || current.cameraPort || !current.cameraUrl
      || port.sender?.url !== current.cameraUrl) {
    port.disconnect();
    return;
  }

  current.cameraPort = port;
  current.cameraTabId = port.sender.tab?.id;
  port.onMessage.addListener(({ state }) => {
    if (typeof state === "string" && state !== "starting" && Object.hasOwn(cameraLabels, state)) {
      reportCamera(current, state);
    }
  });
  port.onDisconnect.addListener(() => {
    if (current.cameraPort !== port) return;
    current.cameraPort = null;
    current.cameraUrl = null;
    current.cameraTabId = null;
    reportCamera(current, "off");
  });
  port.postMessage({ kind: "start" });
});

function send(current, message) {
  try {
    current.port.postMessage(message);
  } catch {
    stop(current, "ERR", "Agent와 연결이 끊겼습니다");
  }
}

chrome.action.onClicked.addListener((tab) => {
  if (watch) {
    stop(watch, "", "사용자가 감시 중지 · 클릭해서 시험 탭 다시 지정");
    return;
  }
  if (!Number.isInteger(tab.id) || !Number.isInteger(tab.windowId)) return;

  const port = chrome.runtime.connectNative(nativeHost);
  const current = { port, tabId: tab.id, windowId: tab.windowId, activeId: tab.id,
    trusted: false, away: false, closing: false, timer: null, lastReply: performance.now(), armedAt: performance.now(),
    cameraState: "starting", cameraUrl: null, cameraPort: null, cameraTabId: null };
  watch = current;
  display("…", "Agent 연결 중");

  port.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError;
    stop(current, "ERR", error ? "Agent 연결 실패 · Native Messaging 등록 확인" : "Agent 연결 종료");
  });
  port.onMessage.addListener(({ status }) => {
    if (watch !== current) return;
    current.lastReply = performance.now();
    if (status === "waiting_for_exam" && !current.trusted) {
      display("WAIT", "시험 시작 대기 중 · 시작하면 카메라 권한을 확인합니다");
      return;
    }
    if (status !== "monitoring") {
      const labels = {
        exam_off: ["OFF", "Agent 시험 모드가 꺼져 있거나 연결이 갱신되었습니다"],
        tab_closed: ["OFF", "시험 탭이 닫혔습니다 · 클릭해서 다시 지정"],
        tab_already_selected: ["OFF", "다른 Chrome 세션에서 이미 시험 탭을 지정했습니다"],
      };
      stop(current, ...(labels[status] || ["ERR", "Agent 응답을 처리할 수 없습니다"]));
      return;
    }
    if (current.trusted) return;
    current.trusted = true;
    displayMonitoring(current);
    activate(current, current.activeId);
    if (watch === current) void openCamera(current);
  });
  const attach = { kind: "attach", wait: true };
  current.timer = setInterval(() => {
    const now = performance.now();
    if (now - current.lastReply >= replyTimeout) {
      stop(current, "ERR", "Agent 응답이 끊겨 탭과 카메라 감시를 종료했습니다");
    } else if (!current.trusted && now - current.armedAt >= armingTimeout) {
      stop(current, "OFF", "시험 시작 대기가 만료됐습니다 · 시험 탭을 다시 지정해 주세요");
    } else {
      send(current, current.trusted ? { kind: "heartbeat" } : attach);
    }
  }, 2000);
  send(current, attach);
});

function activate(current, tabId) {
  current.activeId = tabId;
  const away = tabId !== current.tabId;
  if (!current.trusted || current.closing || current.away === away) return;
  current.away = away;
  displayMonitoring(current);
  send(current, { kind: "transition", transition: away ? "left" : "returned" });
}

chrome.tabs.onActivated.addListener(({ tabId, windowId }) => {
  if (watch && windowId === watch.windowId) activate(watch, tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (!watch || tabId !== watch.tabId) return;
  if (!watch.trusted) {
    stop(watch, "OFF", "지정한 시험 탭이 닫혔습니다");
    return;
  }
  watch.closing = true;
  closeCamera(watch);
  send(watch, { kind: "transition", transition: "closed" });
});

chrome.tabs.onDetached.addListener((tabId) => {
  if (watch && tabId === watch.tabId) {
    stop(watch, "OFF", "시험 탭이 다른 창으로 이동했습니다 · 클릭해서 다시 지정");
  }
});

chrome.tabs.onReplaced.addListener((addedTabId, removedTabId) => {
  if (!watch) return;
  if (watch.tabId === removedTabId) watch.tabId = addedTabId;
  if (watch.activeId === removedTabId) watch.activeId = addedTabId;
});
