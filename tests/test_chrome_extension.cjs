const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function event() {
  return { addListener(listener) { this.fire = listener; } };
}

function extension() {
  const messages = [];
  const badge = [];
  const windows = [];
  const removed = [];
  let tick;
  let now = 0;
  const port = { onMessage: event(), onDisconnect: event(), disconnect() {},
    postMessage(message) { messages.push(JSON.parse(JSON.stringify(message))); } };
  const chrome = {
    tabs: { onActivated: event(), onRemoved: event(), onDetached: event(), onReplaced: event(),
      async remove(id) { removed.push(id); } },
    action: { onClicked: event(), setBadgeText({ text }) { badge.push(text); }, setTitle() {} },
    runtime: { onConnect: event(), connectNative() { return port; },
      getURL(page) { return `chrome-extension://example/${page}`; } },
    windows: { async create(options) { windows.push(options); return { id: 12 }; } },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../chrome_extension/background.js"), "utf8"),
    { chrome, performance: { now: () => now }, crypto: { randomUUID: () => "test-uuid" },
      setInterval(callback) { tick = callback; return 1; }, clearInterval() {} });
  return { chrome, port, messages, badge, windows, removed, tick: (time = now) => { now = time; tick(); } };
}

const attach = { kind: "attach", wait: true };

test("records a single leave and return without sending any tab metadata", () => {
  const { chrome, port, messages, badge } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10, url: "https://example.com/", title: "Exam" });
  port.onMessage.fire({ status: "monitoring" });
  for (const [tabId, windowId] of [[4, 99], [2, 10], [3, 10], [1, 10]]) {
    chrome.tabs.onActivated.fire({ tabId, windowId });
  }
  assert.deepEqual(messages, [attach, { kind: "transition", transition: "left" },
    { kind: "transition", transition: "returned" }]);
  assert.deepEqual(badge, ["…", "ON", "AWAY", "ON"]);
});

test("replaces tab identity, then reports when the exam tab closes", () => {
  const { chrome, port, messages, badge } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  port.onMessage.fire({ status: "monitoring" });
  chrome.tabs.onReplaced.fire(2, 1);
  chrome.tabs.onActivated.fire({ tabId: 2, windowId: 10 });
  chrome.tabs.onRemoved.fire(2);
  port.onMessage.fire({ status: "tab_closed" });
  assert.deepEqual(messages, [attach, { kind: "transition", transition: "closed" }]);
  assert.equal(badge.at(-1), "OFF");
});

test("requires Agent confirmation and stops recording on disconnect", () => {
  const { chrome, port, messages, badge } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  chrome.tabs.onActivated.fire({ tabId: 2, windowId: 10 });
  assert.deepEqual(messages, [attach]);
  port.onMessage.fire({ status: "exam_off" });
  chrome.tabs.onActivated.fire({ tabId: 1, windowId: 10 });
  assert.equal(badge.at(-1), "OFF");
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  port.onMessage.fire({ status: "monitoring" });
  port.onDisconnect.fire();
  chrome.tabs.onActivated.fire({ tabId: 2, windowId: 10 });
  assert.deepEqual(messages, [attach, attach]);
  assert.equal(badge.at(-1), "ERR");
});

test("a moved exam tab ends monitoring and can be designated in its new window", () => {
  const { chrome, port, messages, badge } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  port.onMessage.fire({ status: "monitoring" });
  chrome.tabs.onDetached.fire(1);
  chrome.tabs.onActivated.fire({ tabId: 4, windowId: 10 });
  assert.equal(badge.at(-1), "OFF");
  chrome.action.onClicked.fire({ id: 1, windowId: 20 });
  port.onMessage.fire({ status: "monitoring" });
  chrome.tabs.onActivated.fire({ tabId: 5, windowId: 20 });
  assert.deepEqual(messages, [attach, attach,
    { kind: "transition", transition: "left" }]);
});

test("arming before an exam waits, then opens and closes only the authorized camera tab", () => {
  const { chrome, port, messages, badge, windows, removed, tick } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  port.onMessage.fire({ status: "waiting_for_exam" });
  assert.equal(badge.at(-1), "WAIT");
  assert.equal(windows.length, 0);
  tick();
  assert.deepEqual(messages, [attach, attach]);
  port.onMessage.fire({ status: "monitoring" });
  assert.equal(windows.length, 1);

  const cameraCommands = [];
  let disconnected = 0;
  const cameraPort = { name: "cloudnet-exam-camera", sender: { url: "bad", tab: { id: 7 } },
    onMessage: event(), onDisconnect: event(), disconnect() { disconnected++; },
    postMessage(message) { cameraCommands.push(message.kind); } };
  chrome.runtime.onConnect.fire(cameraPort);
  assert.equal(disconnected, 1);
  assert.deepEqual(cameraCommands, []);
  cameraPort.sender.url = windows[0].url;
  chrome.runtime.onConnect.fire(cameraPort);
  assert.deepEqual(cameraCommands, ["start"]);
  cameraPort.onMessage.fire({ state: "on" });
  assert.deepEqual(messages.at(-1), { kind: "camera", state: "on" });
  port.onMessage.fire({ status: "exam_off" });
  assert.equal(disconnected, 2);
  assert.deepEqual(removed, [7]);
  assert.equal(badge.at(-1), "OFF");
});

test("a silent Native Messaging connection releases the camera", () => {
  const { chrome, port, badge, windows, removed, tick } = extension();
  chrome.action.onClicked.fire({ id: 1, windowId: 10 });
  port.onMessage.fire({ status: "monitoring" });
  let closed = false;
  chrome.runtime.onConnect.fire({ name: "cloudnet-exam-camera",
    sender: { url: windows[0].url, tab: { id: 7 } }, onMessage: event(), onDisconnect: event(),
    postMessage() {}, disconnect() { closed = true; } });
  tick(8000);
  assert.equal(closed, true);
  assert.deepEqual(removed, [7]);
  assert.equal(badge.at(-1), "ERR");
});
