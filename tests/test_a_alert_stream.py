"""TLS alert subscriptions, durable alert identity and lossless state transitions."""
from datetime import datetime, timezone
import socket
import time

from common.protocol import recv_message, send_message
from tests.test_server_exam_stream import ExamStreamTests


class AlertStreamTests(ExamStreamTests):
    def test_raised_snapshot_gap_and_same_id_recovery(self):
        agent, _ = self.connect('agent')
        subscriber, registered = self.connect(subscribe_alerts=True)
        self.assertTrue(registered['alert_stream_v1'])
        self.assertEqual(recv_message(subscriber)['alerts'], [])
        with self.state.lock:
            self.state.alert_states['PC-01'] = {'cpu_percent': {'since': time.monotonic() - 11,
                                      'last': time.monotonic() - 1, 'active': False}}
        metric = {'type': 'METRICS', 'pc_id': 'PC-01', 'timestamp': datetime.now(timezone.utc).isoformat(),
                  'cpu_percent': 95, 'ram_percent': 10, 'upload_mbps': 0, 'download_mbps': 0}
        send_message(agent, metric)
        raised = recv_message(subscriber)
        self.assertEqual((raised['type'], raised['kind'], raised['state']), ('ALERT_EVENT', 'CPU_HIGH', 'raised'))
        send_message(agent, metric)
        self.barrier(agent)
        subscriber.settimeout(.2)
        with self.assertRaises(socket.timeout):
            recv_message(subscriber)
        subscriber.settimeout(2)
        agent.shutdown(socket.SHUT_RDWR)
        self.threads[0].join(timeout=3)
        offline = recv_message(subscriber)
        self.assertEqual((offline['kind'], offline['state']), ('DEVICE_OFFLINE', 'raised'))
        reconnected, _ = self.connect(subscribe_alerts=True)
        opened = recv_message(reconnected)
        cpu = next(item for item in opened['alerts'] if item['kind'] == 'CPU_HIGH')
        self.assertEqual((cpu['alert_id'], cpu['observation']), (raised['alert_id'], 'unknown'))
        new_agent, _ = self.connect('agent')
        self.assertEqual(recv_message(reconnected)['kind'], 'DEVICE_OFFLINE')
        send_message(new_agent, dict(metric, cpu_percent=84))
        recovered = recv_message(reconnected)
        self.assertEqual((recovered['kind'], recovered['state'], recovered['alert_id']),
                         ('CPU_HIGH', 'resolved', raised['alert_id']))

    def test_subscription_rejects_agent_and_unauthenticated_admin(self):
        _, result = self.connect('agent', subscribe_alerts=True)
        self.assertEqual(result['type'], 'ERROR')
        client = self.open_tls()
        send_message(client, {'type': 'REGISTER', 'role': 'admin', 'token': 'wrong', 'subscribe_alerts': True})
        self.assertEqual(recv_message(client)['type'], 'ERROR')
