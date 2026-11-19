"""Latest-only JPEG frames for the selected exam camera."""
import base64
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import select
import socket
from threading import Condition, Lock, Thread
import time

from PIL import Image

from common.protocol import recv_message, send_message
from common.security import connect

FRAME_TIMEOUT = 3
MAX_JPEG_BYTES = 24 * 1024
MAX_FRAME_SIZE = (320, 240)


@dataclass(frozen=True)
class CameraFrame:
    observed: datetime
    received: float
    jpeg: bytes

    def expired(self, now):
        return now - self.received >= FRAME_TIMEOUT

    def image(self):
        with Image.open(BytesIO(self.jpeg), formats=("JPEG",)) as image:
            width, height = image.size
            if not (1 <= width <= MAX_FRAME_SIZE[0] and 1 <= height <= MAX_FRAME_SIZE[1]):
                raise ValueError("시험 영상 크기 오류")
            return image.convert("RGB")


class LatestCameraFrame:
    def __init__(self):
        self._lock = Lock()
        self._target = None
        self._frame = None
        self._last_observed = None

    def select(self, target):
        with self._lock:
            if self._target != target:
                self._target = target
                self._frame = None
                self._last_observed = None

    def publish(self, generation, message, parse_timestamp):
        received = time.monotonic()
        with self._lock:
            if self._target is None or self._target[:3] != (generation, message.get("pc_id"), message.get("exam_id")):
                return
            data = message.get("data")
            if (type(message.get("version")) is not int or message["version"] != 1
                    or message.get("format") != "jpeg" or not isinstance(data, str)
                    or not 0 < len(data) <= 4 * (MAX_JPEG_BYTES // 3)):
                return
            try:
                observed = parse_timestamp(message.get("timestamp"))
                jpeg = base64.b64decode(data, validate=True)
            except (ValueError, OverflowError):
                return
            if jpeg.startswith(b"\xff\xd8") and (self._last_observed is None or observed > self._last_observed):
                self._frame = CameraFrame(observed, received, jpeg)
                self._last_observed = observed

    def take(self):
        with self._lock:
            frame, self._frame = self._frame, None
            return frame


class CameraClient:
    """Maintain one scoped TLS subscription and discard frames on scope changes."""
    def __init__(self, host, port, context, token, server_name, parse_timestamp):
        self.address = host, port, context, server_name
        self.token = token
        self.parse_timestamp = parse_timestamp
        self.frames = LatestCameraFrame()
        self.condition = Condition()
        self.target = None
        self.revision = 0
        self.sock = None
        self.connected = False
        self.closed = False
        self.worker = Thread(target=self.run, daemon=True)
        self.worker.start()

    def select(self, target):
        with self.condition:
            if target == self.target or self.closed:
                return
            self.target = target
            self.revision += 1
            self.connected = False
            self.frames.select(None)
            self.disconnect()
            self.condition.notify()

    def disconnect(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def is_current(self, revision):
        return not self.closed and revision == self.revision

    def take(self):
        return self.frames.take()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.target is not None)
                if self.closed:
                    return
                target, revision = self.target, self.revision
            try:
                self.stream(target, revision)
            except (OSError, EOFError, ValueError, UnicodeError, OverflowError):
                pass
            finally:
                with self.condition:
                    self.sock = None
                    self.connected = False
                    self.frames.select(None)
                    self.condition.wait_for(lambda: not self.is_current(revision), timeout=2)

    def stream(self, target, revision):
        generation, pc_id, exam_id, *_ = target
        with connect(*self.address) as conn:
            with self.condition:
                if not self.is_current(revision):
                    return
                self.sock = conn
            send_message(conn, dict(type="REGISTER", role="admin", token=self.token,
                                    subscribe_camera=dict(pc_id=pc_id, exam_id=exam_id)))
            response = recv_message(conn)
            if (response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True
                    or response.get("exam_camera_stream_v1") is not True):
                return
            with self.condition:
                if not self.is_current(revision):
                    return
                self.frames.select(target)
                self.connected = True
            next_poll = time.monotonic()
            deadline = None
            while True:
                with self.condition:
                    if not self.is_current(revision):
                        return
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise TimeoutError()
                if deadline is None and now >= next_poll:
                    send_message(conn, {"type": "LIST_DEVICES"})
                    deadline = now + 5
                    next_poll = now + 2
                if not conn.pending() and not select.select([conn], [], [], .2)[0]:
                    continue
                conn.settimeout(3)
                message = recv_message(conn)
                if message.get("type") == "DEVICE_LIST":
                    deadline = None
                    continue
                if message.get("type") != "EXAM_CAMERA_FRAME":
                    raise ValueError("시험 영상 구독 응답 오류")
                with self.condition:
                    if not self.is_current(revision):
                        return
                    self.frames.publish(generation, message, self.parse_timestamp)

    def close(self):
        with self.condition:
            self.closed = True
            self.connected = False
            self.frames.select(None)
            self.disconnect()
            self.condition.notify()
