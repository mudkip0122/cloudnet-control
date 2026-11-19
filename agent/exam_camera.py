"""Bounded, in-memory handoff of camera frames from the local Chrome host."""
import base64
import binascii
import socket


MAX_JPEG_BYTES = 24 * 1024
MAX_BASE64_BYTES = MAX_JPEG_BYTES * 4 // 3
SESSION_BYTES = 16


def jpeg_bytes(value):
    if not isinstance(value, str) or len(value) > MAX_BASE64_BYTES:
        return None
    try:
        image = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    if image.startswith(b"\xff\xd8") and image.endswith(b"\xff\xd9"):
        return image
    return None


def forward_frame(port, session, image):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(bytes.fromhex(session) + image, ("127.0.0.1", port))


class CameraFrames:
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.bind(("127.0.0.1", 0))
            self.socket.setblocking(False)
        except OSError:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]

    def latest(self, session):
        prefix = bytes.fromhex(session)
        image = None
        for _ in range(16):
            try:
                packet = self.socket.recv(65535)
            except BlockingIOError:
                break
            if (SESSION_BYTES < len(packet) <= SESSION_BYTES + MAX_JPEG_BYTES
                    and packet.startswith(prefix)):
                image = packet[SESSION_BYTES:]
        if image is not None:
            return base64.b64encode(image).decode("ascii")
        return None

    def close(self):
        self.socket.close()
