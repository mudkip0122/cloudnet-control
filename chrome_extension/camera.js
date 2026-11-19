const statusText = document.querySelector("#status");
const preview = document.querySelector("#preview");
const port = chrome.runtime.connect({ name: "cloudnet-exam-camera" });
let state = "waiting";
let stream = null;

function update(next, text, notify = true) {
  state = next;
  statusText.textContent = text;
  if (!notify) return;
  try {
    port.postMessage({ state: next });
  } catch {
    stop();
  }
}

function stopTracks(media) {
  for (const track of media.getTracks()) track.stop();
}

function release() {
  if (stream) stopTracks(stream);
  stream = null;
  preview.srcObject = null;
  preview.hidden = true;
}

function stop() {
  state = "off";
  release();
  update("off", "시험 연결이 종료되어 카메라를 껐습니다.", false);
}

async function start() {
  if (state !== "waiting") return;
  update("starting", "Chrome에서 카메라 사용 권한을 요청하면 허용을 선택해 주세요.", false);
  try {
    const media = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { width: { ideal: 480 }, height: { ideal: 360 }, frameRate: { ideal: 12, max: 20 } },
    });
    if (state !== "starting") {
      stopTracks(media);
      return;
    }
    stream = media;
    const tracks = media.getVideoTracks().filter((track) => track.readyState === "live");
    if (!tracks.length) throw new Error("Camera video track unavailable");
    for (const track of tracks) {
      track.addEventListener("ended", () => {
        if (state !== "on") return;
        release();
        update("unavailable", "카메라 연결이 끊겼습니다.");
      });
    }
    preview.srcObject = media;
    preview.hidden = false;
    update("on", "카메라가 켜져 있습니다.");
  } catch (error) {
    if (state !== "starting") return;
    release();
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      update("permission_denied", "Chrome 또는 Windows에서 카메라 권한을 허용하지 않았습니다.");
    } else {
      update("unavailable", "카메라가 없거나 다른 앱에서 사용 중입니다.");
    }
  }
}

port.onMessage.addListener(({ kind }) => {
  if (kind === "start") void start();
});
port.onDisconnect.addListener(stop);
window.addEventListener("pagehide", stop, { once: true });
