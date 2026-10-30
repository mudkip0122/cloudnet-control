"""Shared UTF-8 JSON framing for the local development prototype."""
import json
import struct

MAX_MESSAGE_BYTES = 65536


def _read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("Connection closed before message completed")
        data.extend(chunk)
    return bytes(data)


def send_message(sock, message):
    if not isinstance(message, dict):
        raise ValueError("Message must be an object")
    payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ValueError("Invalid message size")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_message(sock):
    size = struct.unpack("!I", _read_exact(sock, 4))[0]
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise ValueError("Invalid message size")
    def reject_constant(value):
        raise ValueError("Non-finite JSON number: " + value)
    message = json.loads(_read_exact(sock, size).decode("utf-8"), parse_constant=reject_constant)
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("Message requires a string type")
    return message
