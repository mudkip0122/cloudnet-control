"""Latest-only JPEG frames for the selected exam camera."""
import base64
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from threading import Lock
import time

from PIL import Image

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
