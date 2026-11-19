import tempfile
import time
import unittest
import socket
import threading
import sqlite3
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from server.audit import AuditStore
from server.main import MonitoringServer
from server.contracts import exam_event
from admin_gui.history_query import collect_history
from common.protocol import send_message, recv_message


class Receiver:
    def send(self, message):
        self.message = message


class Week4Tests(unittest.TestCase):
    def test_open_alerts_and_devices_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit.db'
            store = AuditStore(path)
            server = MonitoringServer(audit=store)
            store.register_device('PC-01', 'test PC', time.time())
            server.devices['PC-01'] = {}
            message = {'pc_id': 'PC-01', 'timestamp': '2026-11-19T00:00:00Z',
                       'cpu_percent': 95, 'ram_percent': 10, 'upload_mbps': 0, 'download_mbps': 0}
            for instant in (0, 5, 10):
                with patch('server.main.time.monotonic', return_value=instant):
                    server.update_metrics('PC-01', message)
            store.close()

            store = AuditStore(path)
            server = MonitoringServer(audit=store)
            opened = server.alert_snapshot_locked()
            cpu = next(alert for alert in opened if alert['kind'] == 'CPU_HIGH')
            self.assertEqual(cpu['observation'], 'unknown')
            self.assertEqual({alert['kind'] for alert in opened}, {'CPU_HIGH', 'DEVICE_OFFLINE'})
            store.register_device('PC-01', 'test PC', time.time())
            server.devices['PC-01'] = {}
            with patch('server.main.time.monotonic', return_value=20):
                server.update_metrics('PC-01', dict(message, cpu_percent=95))
            with patch('server.main.time.monotonic', return_value=25):
                server.update_metrics('PC-01', dict(message, cpu_percent=84))
            events, _ = store.history('PC-01', 0, time.time() + 10, 'events', 100)
            by_kind = {}
            for item in reversed(events):
                event = item['data']
                by_kind.setdefault(event['kind'], []).append(event)
            for kind in ('CPU_HIGH', 'DEVICE_OFFLINE'):
                self.assertEqual([event['state'] for event in by_kind[kind]], ['raised', 'resolved'])
                self.assertEqual(by_kind[kind][0]['alert_id'], by_kind[kind][1]['alert_id'])
            store.close()

    def test_concurrent_writes_and_transaction_rollback(self):
        store = AuditStore(':memory:')
        now = time.time()
        def write(index):
            store.metric('PC-01' if index % 2 else 'PC-02', now, {'index': index}, [])
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(write, range(80)))
        first, _ = store.history('PC-01', now - 1, now + 1, 'metrics', 100)
        second, _ = store.history('PC-02', now - 1, now + 1, 'metrics', 100)
        self.assertEqual((len(first), len(second)), (40, 40))
        self.assertEqual(len({row['received_epoch'] for row in first}), 40)
        with self.assertRaises(KeyError):
            store.metric('PC-01', now + 2, {'index': 999}, [{'kind': 'CPU_HIGH'}])
        rows, _ = store.history('PC-01', now + 1, now + 3, 'metrics', 100)
        self.assertEqual(rows, [])
        store.close()

    def test_chrome_timeline_validation_strips_unexpected_fields(self):
        base = {'type': 'EXAM_EVENT', 'version': 1, 'event_id': 'event-1', 'pc_id': 'PC-01',
                'exam_id': 'exam-1', 'timestamp': '2026-11-19T00:00:00Z'}
        for extra in ({'kind': 'EXAM_TAB_CHANGED', 'transition': 'left'},
                      {'kind': 'EXAM_CAMERA_STATUS', 'status': 'on'},
                      {'kind': 'EXAM_TAB_MONITOR_STATUS', 'status': 'unavailable',
                       'reason': 'event_buffer_overflow', 'dropped_count': 2}):
            clean = exam_event(base | extra | {'url': 'private'}, 'PC-01')
            self.assertNotIn('url', clean)
        with self.assertRaises(ValueError):
            exam_event(base | {'kind': 'EXAM_TAB_MONITOR_STATUS', 'status': 'monitoring',
                               'reason': 'extension_connected', 'dropped_count': 1}, 'PC-01')

    def test_persistence_filter_limit_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit.db'
            store = AuditStore(path)
            now = time.time()
            for n in range(3):
                store.metric('PC-01', now + n, {'n': n}, [])
            store.monitoring_event('PC-02', 'OFFLINE', now)
            store.close()
            store = AuditStore(path)
            server = MonitoringServer(audit=store)
            receiver = Receiver()
            query = {'pc_id': 'PC-01', 'kind': 'metrics', 'start_epoch': now - 1,
                     'end_epoch': now + 4, 'limit': 2}
            server.history(receiver, query)
            self.assertEqual([row['data']['n'] for row in receiver.message['rows']], [2, 1])
            self.assertTrue(receiver.message['truncated'])
            server.history(receiver, dict(query, limit=True))
            self.assertEqual(receiver.message['code'], 'INVALID_REQUEST')
            server.history(receiver, dict(query, pc_id='PC-02'))
            self.assertEqual(receiver.message['rows'], [])
            store.close()

    def test_sustained_threshold_and_recovery(self):
        store = AuditStore(':memory:')
        server = MonitoringServer(audit=store)
        server.devices['PC-01'] = {}
        message = {'pc_id': 'PC-01', 'timestamp': '2026-11-19T00:00:00Z',
                   'cpu_percent': 95, 'ram_percent': 10, 'upload_mbps': 0, 'download_mbps': 0}
        for instant in (0, 5, 10, 15):
            with patch('server.main.time.monotonic', return_value=instant):
                server.update_metrics('PC-01', message)
        with patch('server.main.time.monotonic', return_value=20):
            server.update_metrics('PC-01', dict(message, cpu_percent=84))
        rows, _ = store.history('PC-01', 0, time.time() + 5, 'events', 100)
        self.assertEqual([row['data']['state'] for row in rows], ['resolved', 'raised'])
        self.assertEqual(rows[0]['data']['alert_id'], rows[1]['data']['alert_id'])
        store.close()

    def test_c_history_client_reads_multiple_pages(self):
        store = AuditStore(':memory:')
        server = MonitoringServer(audit=store)
        now = datetime.now(timezone.utc)
        request = {'version': 1, 'request_id': 'request-1', 'pc_id': 'PC-01',
                   'start_at': (now - timedelta(minutes=10)).isoformat(), 'end_at': now.isoformat()}
        metric = {'cpu_percent': 10, 'ram_percent': 20, 'upload_mbps': None, 'download_mbps': 0}
        for n in range(205):
            store.metric('PC-01', now.timestamp() - 300 + n, metric, [])
        store.monitoring_event('PC-01', 'CPU_HIGH', now.timestamp() - 1,
                               {'kind': 'CPU_HIGH', 'state': 'raised', 'alert_id': 'alert-1'})
        class Pages:
            def __init__(self):
                self.pages = []
            def send(self, message):
                self.pages.append(message)
        receiver = Pages()
        server.history_v1(receiver, request)
        self.assertEqual(len(receiver.pages), 2)
        pages = iter(receiver.pages)
        points, alerts = collect_history(request, lambda: next(pages))
        self.assertEqual((len(points), len(alerts)), (205, 1))
        self.assertEqual(alerts[0][1:], ('CPU_HIGH', 'raised'))
        store.close()

    def test_history_over_authenticated_connection(self):
        store = AuditStore(':memory:')
        server = MonitoringServer({'admin_token': 'test-token', 'agents': {}}, audit=store)
        now = datetime.now(timezone.utc)
        store.metric('PC-01', now.timestamp() - 1,
                     {'cpu_percent': 1, 'ram_percent': 2, 'upload_mbps': 0, 'download_mbps': None}, [])
        client, peer = socket.socketpair()
        thread = threading.Thread(target=server.handle, args=(peer,), daemon=True)
        thread.start()
        try:
            client.settimeout(3)
            send_message(client, {'type': 'REGISTER', 'role': 'admin', 'token': 'test-token'})
            self.assertTrue(recv_message(client)['ok'])
            request = {'type': 'GET_HISTORY', 'version': 1, 'request_id': 'test', 'pc_id': 'PC-01',
                       'start_at': (now - timedelta(minutes=10)).isoformat(), 'end_at': now.isoformat()}
            send_message(client, request)
            points, alerts = collect_history(request, lambda: recv_message(client))
            self.assertEqual((len(points), len(alerts)), (1, 0))
        finally:
            client.close()
            thread.join(3)
            peer.close()
            store.close()
        self.assertFalse(thread.is_alive())

    def test_history_pages_respect_frame_limit_and_report_database_failure(self):
        store = AuditStore(':memory:')
        server = MonitoringServer(audit=store)
        now = datetime.now(timezone.utc)
        request = {'version': 1, 'request_id': 'test', 'pc_id': 'PC-01',
                   'start_at': (now - timedelta(minutes=10)).isoformat(), 'end_at': now.isoformat()}
        metric = {'cpu_percent': 1, 'ram_percent': 2, 'upload_mbps': 10 ** 300, 'download_mbps': 10 ** 300}
        for n in range(200):
            store.metric('PC-01', now.timestamp() - 300 + n, metric, [])
        class Pages:
            def __init__(self):
                self.pages = []
            def send(self, message):
                self.pages.append(message)
        receiver = Pages()
        server.history_v1(receiver, request)
        self.assertGreater(len(receiver.pages), 1)
        self.assertTrue(all(len(json.dumps(page).encode()) <= 65536 for page in receiver.pages))
        with patch.object(store, 'history_snapshot', side_effect=sqlite3.OperationalError()):
            server.history_v1(receiver, request)
        self.assertEqual(receiver.pages[-1]['code'], 'STORAGE_UNAVAILABLE')
        store.close()


if __name__ == '__main__':
    unittest.main()
