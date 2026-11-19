const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../chrome_extension/camera.js"), "utf8");

function camera(getMedia) {
  const actions = {};
  const messages = [];
  const preview = { hidden: true, srcObject: null, videoWidth: 480, videoHeight: 360 };
  const status = { textContent: "" };
  const details = { textContent: "" };
  const frames = [];
  const canvas = { getContext: () => ({ drawImage() {} }),
    toDataURL: () => "data:image/jpeg;base64,/9j/2Q==" };
  const requests = [];
  const port = {
    onMessage: { addListener(callback) { actions.receive = callback; } },
    onDisconnect: { addListener(callback) { actions.disconnect = callback; } },
    postMessage(message) {
      if (message.kind === "camera_frame") frames.push(message.data);
      else messages.push(message.state);
    },
  };
  vm.runInNewContext(script, {
    chrome: { runtime: { connect() { return port; } } },
    document: {
      querySelector(selector) {
        if (selector === "#preview") return preview;
        return selector === "#camera-details" ? details : status;
      },
      createElement() { return canvas; },
    },
    setInterval(callback) { actions.tick = callback; return 1; },
    clearInterval() { actions.tick = null; },
    window: { addEventListener(_, callback) { actions.hide = callback; } },
    navigator: { mediaDevices: { getUserMedia(options) { requests.push(options); return getMedia(); } } },
  });
  return { actions, requests, preview, messages, frames, canvas, details };
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
  assert.equal(actions.tick, undefined);
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

test("shares bounded JPEG frames only after camera opens and stops when the view closes", async () => {
  const { media } = videoStream();
  const { actions, frames, canvas, details } = camera(async () => media);
  actions.receive({ kind: "start", shareCamera: true });
  assert.match(details.textContent, /전송됩니다/);
  assert.equal(actions.tick, undefined);
  await settle();
  const pendingTick = actions.tick;
  pendingTick();
  assert.deepEqual(frames, ["/9j/2Q=="]);
  assert.equal(canvas.width, 320);
  assert.equal(canvas.height, 240);
  actions.hide();
  assert.equal(actions.tick, null);
  pendingTick();
  assert.equal(frames.length, 1);
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
