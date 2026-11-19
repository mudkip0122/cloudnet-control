"""Apply two prevalidated A files over SSH; PAYLOAD is supplied by the caller."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time

ROOT = Path('/home/ubuntu/cloudnet-direct')
UNIT = 'cloudnet-control-launcher.service'
data = json.loads(base64.b64decode(PAYLOAD))


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def protected():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).digest()
            for folder in ('agent', 'admin_gui', 'secrets')
            for path in (ROOT / folder).rglob('*')
            if path.is_file() and '__pycache__' not in path.parts}


def verify():
    sys.path.insert(0, str(ROOT))
    from common.protocol import recv_message, send_message
    auth = json.loads((ROOT / 'secrets/ec2/auth.json').read_text())
    certificate = ROOT / 'secrets/ec2/server.crt'
    identity = next(v for k, v in ssl._ssl._test_decode_cert(str(certificate))['subjectAltName']
                    if k in ('IP Address', 'DNS'))
    context = ssl.create_default_context(cafile=str(certificate))
    with socket.create_connection(('127.0.0.1', 5000), timeout=8) as raw:
        with context.wrap_socket(raw, server_hostname=identity) as conn:
            conn.settimeout(8)
            send_message(conn, {'type': 'REGISTER', 'role': 'admin', 'token': auth['admin_token']})
            reply = recv_message(conn)
            if not all(reply.get(key) is True for key in ('ok', 'exam_events_v1', 'exam_chrome_timeline_v1')):
                raise RuntimeError('Expected TLS capability is missing')
            send_message(conn, {'type': 'LIST_DEVICES'})
            listing = recv_message(conn)
            if listing.get('type') != 'DEVICE_LIST':
                raise RuntimeError('Device list unavailable')
            return [{'pc_id': item['pc_id'], 'online': item['online']} for item in listing['devices']]


if command('systemctl', 'is-active', UNIT).stdout.strip() != 'active':
    raise RuntimeError('Service was not active; no changes applied')
for name, entry in data.items():
    target = ROOT / name
    if hashlib.sha256(target.read_bytes()).hexdigest() != entry['before']:
        raise RuntimeError('Live source differs from the tested baseline: ' + name)
    compile(base64.b64decode(entry['after']), name, 'exec')
before = protected()
backup = ROOT.parent / 'cloudnet-backups' / ('chrome-timeline-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
backup.mkdir(parents=True, mode=0o700)
for name in data:
    destination = backup / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / name, destination)
try:
    command('sudo', '-n', 'systemctl', 'stop', UNIT)
    for name, entry in data.items():
        target = ROOT / name
        temporary = target.with_name(target.name + '.chrome-timeline.tmp')
        temporary.write_bytes(base64.b64decode(entry['after']))
        os.replace(temporary, target)
    command('sudo', '-n', 'systemctl', 'start', UNIT)
    for attempt in range(30):
        try:
            devices = verify()
            break
        except (OSError, ConnectionError):
            if attempt == 29:
                raise
            time.sleep(.2)
    if command('systemctl', 'is-active', UNIT).stdout.strip() != 'active' or protected() != before:
        raise RuntimeError('Service or protected-file verification failed')
    print(json.dumps({'active': True, 'exam_chrome_timeline_v1': True, 'backup': str(backup),
                      'protected_files_unchanged': True, 'devices': devices}))
except BaseException:
    subprocess.run(['sudo', '-n', 'systemctl', 'stop', UNIT], capture_output=True)
    for name in data:
        shutil.copy2(backup / name, ROOT / name)
    subprocess.run(['sudo', '-n', 'systemctl', 'start', UNIT], capture_output=True)
    print(json.dumps({'rolled_back': True, 'backup': str(backup)}), flush=True)
    raise
