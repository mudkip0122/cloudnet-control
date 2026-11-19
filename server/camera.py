"""Bounded camera-frame validation, with no frame logging or persistence."""
import base64
import binascii
from datetime import datetime

from server.contracts import exam_id, text

MAX_JPEG = 24 * 1024


def jpeg_dimensions(image):
    if len(image) < 4 or image[:2] != b'\xff\xd8' or image[-2:] != b'\xff\xd9':
        raise ValueError('Invalid JPEG')
    offset = 2
    while offset + 4 <= len(image):
        if image[offset] != 0xff:
            break
        while offset < len(image) and image[offset] == 0xff:
            offset += 1
        if offset >= len(image):
            break
        marker = image[offset]
        offset += 1
        if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
            continue
        if marker in (0xd9, 0xda) or offset + 2 > len(image):
            break
        size = int.from_bytes(image[offset:offset + 2], 'big')
        if size < 2 or offset + size > len(image):
            break
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if size < 8:
                break
            height = int.from_bytes(image[offset + 3:offset + 5], 'big')
            width = int.from_bytes(image[offset + 5:offset + 7], 'big')
            if 0 < width <= 320 and 0 < height <= 240:
                return width, height
            break
        offset += size
    raise ValueError('Invalid JPEG dimensions')


def camera_frame(message, pc_id):
    if (message.get('pc_id') != pc_id or type(message.get('version')) is not int or message['version'] != 1
            or not exam_id(message.get('exam_id')) or message.get('format') != 'jpeg'):
        raise ValueError('Invalid camera frame identity')
    stamp = message.get('timestamp')
    if not text(stamp, 64):
        raise ValueError('Invalid camera timestamp')
    if datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Camera timestamp requires timezone')
    data = message.get('data')
    if not isinstance(data, str) or len(data) > MAX_JPEG * 4 // 3:
        raise ValueError('Invalid camera frame size')
    try:
        image = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Invalid camera frame encoding') from None
    if len(image) > MAX_JPEG:
        raise ValueError('Invalid camera frame size')
    jpeg_dimensions(image)
    return {key: message[key] for key in ('type', 'version', 'pc_id', 'exam_id', 'timestamp', 'format', 'data')}
