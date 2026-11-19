"""Read-only production history check; executed over SSH without printing credentials."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import ssl
import sys

ROOT = Path('/home/ubuntu/cloudnet-direct')
sys.path.insert(0, str(ROOT))
from common.protocol import send_message, recv_message

credentials = json.loads((ROOT / 'secrets/ec2/auth.json').read_text())
certificate = ROOT / 'secrets/ec2/server.crt'
identity = next(value for kind, value in ssl._ssl._test_decode_cert(str(certificate))['subjectAltName']
                if kind in ('IP Address', 'DNS'))
context = ssl.create_default_context(cafile=str(certificate))
end = datetime.now(timezone.utc)
results = {}
with socket.create_connection(('127.0.0.1', 5000), timeout=8) as raw:
    with context.wrap_socket(raw, server_hostname=identity) as conn:
        conn.settimeout(8)
        send_message(conn, {'type': 'REGISTER', 'role': 'admin', 'token': credentials['admin_token']})
        reply = recv_message(conn)
        if reply.get('ok') is not True or reply.get('history_v1') is not True:
            raise RuntimeError('History capability unavailable')
        for pc_id in ('PC-01', 'PC-02', 'PC-03'):
            send_message(conn, {'type': 'GET_HISTORY', 'version': 1, 'request_id': 'verify-' + pc_id,
                                'pc_id': pc_id, 'start_at': (end - timedelta(minutes=10)).isoformat(),
                                'end_at': end.isoformat()})
            count = 0
            for page in range(64):
                reply = recv_message(conn)
                if reply.get('ok') is not True or reply.get('pc_id') != pc_id or reply.get('page') != page:
                    raise RuntimeError('History query failed')
                count += len(reply['metrics'])
                if reply.get('done') is True:
                    break
            else:
                raise RuntimeError('History response exceeded page limit')
            results[pc_id] = count
print(json.dumps({'history_metric_counts': results}))
