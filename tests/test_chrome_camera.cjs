const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../chrome_extension/camera.js"), "utf8");

function camera(getMedia) {
  const actions = {};
  const messages = [];
  const preview = { hidden: true, srcObject: null };
  const status = { textContent: "" };
  const requests = [];
  const port = {
    onMessage: { addListener(callback) { actions.receive = callback; } },
    onDisconnect: { addListener(callback) { actions.disconnect = callback; } },
    postMessage(message) { messages.push(message.state); },
  };
  vm.runInNewContext(script, {
    chrome: { runtime: { connect() { return port; } } },
    document: { querySelector(selector) { return selector === "#preview" ? preview : status; } },
    window: { addEventListener(_, callback) { actions.hide = callback; } },
    navigator: { mediaDevices: { getUserMedia(options) { requests.push(options); return getMedia(); } } },
  });
  return { actions, requests, preview, messages };
}

function videoStream() {
  const track = { readyState: "live", stopped: 0, stop() { this.stopped++; },
    addEventListener(_, callback) { this.ended = callback; } };
  return { track, media: { getTracks: () => [track], getVideoTracks: () => [track] } };
}

const settle = () => new Promise(setImmediate);

test("starts only when told and releases the video stream on disconnect", async () => {
  const { media, track } = videoStream();
  const { actions, requests, preview, messages } = camera(async () => media);
  assert.deepEqual(requests, []);
  actions.receive({ kind: "start" });
  actions.receive({ kind: "start" });
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].audio, false);
  assert.equal(preview.srcObject, media);
  assert.deepEqual(messages, ["on"]);
  actions.disconnect();
  assert.equal(track.stopped, 1);
  assert.equal(preview.srcObject, null);
  assert.equal(preview.hidden, true);
});

test("immediately stops a late permission grant after the exam ends", async () => {
  const { track, media } = videoStream();
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const { actions, preview, messages } = camera(() => pending);
  actions.receive({ kind: "start" });
  actions.disconnect();
  resolve(media);
  await settle();
  assert.equal(track.stopped, 1);
  assert.equal(preview.srcObject, null);
  assert.deepEqual(messages, []);
});

test("distinguishes denied permission from a missing or busy camera", async () => {
  for (const [name, state] of [["NotAllowedError", "permission_denied"], ["NotReadableError", "unavailable"]]) {
    const { actions, messages, preview } = camera(async () => { throw { name }; });
    actions.receive({ kind: "start" });
    await settle();
    assert.deepEqual(messages, [state]);
    assert.equal(preview.hidden, true);
  }
});

test("releases the preview when the device disappears or the page closes", async () => {
  for (const stop of ["disappear", "hide"]) {
    const { track, media } = videoStream();
    const { actions, messages, preview } = camera(async () => media);
    actions.receive({ kind: "start" });
    await settle();
    if (stop === "disappear") track.ended();
    else actions.hide();
    assert.equal(track.stopped, 1);
    assert.equal(preview.hidden, true);
    assert.deepEqual(messages, stop === "disappear" ? ["on", "unavailable"] : ["on"]);
  }
});
