const statusText = document.querySelector("#status");
const preview = document.querySelector("#preview");
const port = chrome.runtime.connect({ name: "cloudnet-exam-camera" });
let state = "waiting";
let stream = null;
let frameTimer = null;

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
  if (frameTimer !== null) clearInterval(frameTimer);
  frameTimer = null;
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

function shareFrames() {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (context === null) return;
  frameTimer = setInterval(() => {
    if (state !== "on" || !preview.videoWidth || !preview.videoHeight) return;
    const scale = Math.min(1, 320 / preview.videoWidth, 240 / preview.videoHeight);
    canvas.width = Math.max(1, Math.round(preview.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(preview.videoHeight * scale));
    context.drawImage(preview, 0, 0, canvas.width, canvas.height);
    const prefix = "data:image/jpeg;base64,";
    let image = canvas.toDataURL("image/jpeg", 0.65);
    if (image.length > prefix.length + 32768) image = canvas.toDataURL("image/jpeg", 0.4);
    if (!image.startsWith(prefix) || image.length > prefix.length + 32768) return;
    try {
      port.postMessage({ kind: "camera_frame", data: image.slice(prefix.length) });
    } catch {
      stop();
    }
  }, 500);
}

async function start(shareCamera) {
  if (state !== "waiting") return;
  if (shareCamera) {
    document.querySelector("#camera-details").textContent =
      "카메라 영상이 시험 감독자에게 전송됩니다. 시험이 끝나면 카메라가 자동으로 꺼집니다.";
  }
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
    if (shareCamera && state === "on") shareFrames();
  } catch (error) {
    if (state !== "starting" && state !== "on") return;
    release();
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      update("permission_denied", "Chrome 또는 Windows에서 카메라 권한을 허용하지 않았습니다.");
    } else {
      update("unavailable", "카메라가 없거나 다른 앱에서 사용 중입니다.");
    }
  }
}

port.onMessage.addListener(({ kind, shareCamera }) => {
  if (kind === "start") void start(shareCamera === true);
});
port.onDisconnect.addListener(stop);
window.addEventListener("pagehide", stop, { once: true });
