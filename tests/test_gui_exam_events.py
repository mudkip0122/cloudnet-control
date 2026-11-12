"""C subscription contract tests. Socket peers simulate the agreed A transport."""
from datetime import datetime, timezone
import socket
import time
import unittest
from unittest.mock import patch

from admin_gui.exam_events import ExamEventClient, ExamStore, exam_event
from admin_gui.presentation import device_view
from common.protocol import recv_message, send_message


def detection(event_id='e1', pc_id='PC-01', exam_id='exam-1'):
    return dict(type='EXAM_EVENT', version=1, event_id=event_id, pc_id=pc_id, exam_id=exam_id,
        timestamp=datetime.now(timezone.utc).isoformat(), kind='FORBIDDEN_PROCESS_DETECTED',
        process=dict(pid=123, name='discord.exe', create_time=1234.5))


def mode(enabled=True):
    return dict(enabled=enabled, exam_id='exam-1', forbidden_processes=['discord.exe'] if enabled else [],
                poll_interval=1 if enabled else None, monitor_status='starting' if enabled else 'off')


class ExamStoreTests(unittest.TestCase):
    def test_local_history_removal_preserves_state_and_dedup(self):
        store = ExamStore()
        store.snapshot([dict(pc_id='PC-01', online=True, exam_mode=mode())])
        first, second = detection('first'), detection('second', pc_id='PC-02')
        store.accept(first)
        store.accept(second)
        self.assertEqual(store.remove_history(['second']), 1)
        self.assertEqual(store.latest_warning['event_id'], 'first')
        self.assertFalse(store.accept(second))
        self.assertEqual(store.remove_history(['first']), 1)
        self.assertIsNone(store.latest_warning)
        self.assertTrue(store.devices['PC-01']['exam_mode']['enabled'])
        self.assertFalse(store.accept(first))
        self.assertTrue(store.accept(detection('restored')))
        self.assertEqual([e['event_id'] for e in store.history], ['restored'])

    def test_dedup_history_is_bounded_and_retained_through_disconnect(self):
        store = ExamStore(limit=3, dedup_limit=4)
        for index in range(6):
            self.assertTrue(store.accept(detection(str(index))))
        self.assertFalse(store.accept(detection('5')))
        self.assertEqual([e['event_id'] for e in store.history], ['3', '4', '5'])
        self.assertEqual(len(store.seen), 4)
        store.invalidate()
        self.assertEqual(len(store.history), 3)
        self.assertEqual(store.latest_warning['event_id'], '5')

    def test_current_mode_is_authoritative_snapshot_not_detection(self):
        store = ExamStore()
        store.snapshot([dict(pc_id='PC-01', online=True)])
        store.accept(detection())
        self.assertIn('미확인', store.state('PC-01', True))

        store.status_update(dict(pc_id='PC-01', online=True, exam_mode=mode()))
        self.assertIn('검사 시작 중', store.state('PC-01', True))
        store.status_update(dict(pc_id='PC-01', online=True, exam_mode=mode(False)))
        self.assertIn('꺼짐', store.state('PC-01', True))
        self.assertEqual(len(store.history), 1)
        store.status_update(dict(pc_id='PC-01', online=False, exam_mode=mode()))
        self.assertIn('연결 끊김', store.state('PC-01', True))
        store.status_update(dict(pc_id='PC-01', online=True, exam_mode=None))
        self.assertIn('미확인', store.state('PC-01', True))

    def test_restored_same_pid_is_new_detection_when_event_id_changes(self):
        store = ExamStore()
        first, restored = detection('first-display'), detection('restored-display')
        self.assertEqual(first['process'], restored['process'])
        self.assertTrue(store.accept(first))
        self.assertFalse(store.accept(first))
        self.assertTrue(store.accept(restored))
        self.assertFalse(store.accept(restored))
        self.assertEqual([e['event_id'] for e in store.history], ['first-display', 'restored-display'])

    def test_monitor_status_and_pc_exam_isolation(self):
        store = ExamStore()
        store.snapshot([dict(pc_id=pc, online=True, exam_mode=mode()) for pc in ('PC-01', 'PC-02')])
        for index, status in enumerate(('ok', 'partial', 'unavailable')):
            event = detection(str(index))
            event.pop('process')
            event.update(kind='MONITOR_STATUS', status=status, unreadable_count=index, poll_interval=1)
            store.accept(event)
        self.assertEqual(store.monitor['PC-01']['status'], 'unavailable')
        self.assertNotIn('PC-02', store.monitor)
        self.assertIsNone(store.latest_warning)
        store.status_update(dict(pc_id='PC-01', online=True, exam_mode=mode(False)))
        self.assertNotIn('PC-01', store.monitor)

    def test_malformed_events_are_not_accepted(self):
        original = detection()
        bad = [dict(original, version=True), dict(original, exam_id='bad id'),
               dict(original, timestamp='2026-11-12T10:00:00'), dict(original, event_id=''),
               dict(original, process=dict(pid=1, name='x.exe', create_time=float('nan'))),
               dict(original, process=dict(pid=True, name='x.exe', create_time=1))]
        for message in bad:
            with self.assertRaises(ValueError):
                exam_event(message)

    def test_rtt_and_values_hidden_when_server_metrics_stale_even_with_fresh_agent_clock(self):
        stamp = datetime.now(timezone.utc).isoformat()
        device = dict(pc_id='PC-01', pc_name='test', online=True, metrics_stale=True,
            last_metrics_received=stamp, last_received='heartbeat-is-newer', metrics=dict(
                timestamp=stamp, cpu_percent=10, rtt_timestamp=stamp, rtt_ms=1.5, rtt_status='ok'))
        view = device_view(device)
        self.assertEqual((view['cpu'], view['rtt']), ('—', '—'))
        self.assertEqual(view['received'], stamp)


class SubscriptionTests(unittest.TestCase):
    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition())

    def test_separate_registration_and_lossless_interleaved_event_delivery(self):
        client_socket, peer = socket.socketpair()
        client = None
        try:
            with patch('admin_gui.exam_events.connect', return_value=client_socket):
                client = ExamEventClient('localhost', 0, object(), 'test-admin')
                peer.settimeout(3)
                registration = recv_message(peer)
                self.assertEqual(registration, dict(type='REGISTER', role='admin', token='test-admin',
                                                    subscribe_exam=True, subscribe_status=True))
                send_message(peer, dict(type='REGISTER_RESULT', ok=True))
                self.assertEqual(recv_message(peer), {'type': 'LIST_DEVICES'})
                for index in range(80):
                    send_message(peer, detection(str(index)))
                send_message(peer, dict(type='STATUS_UPDATE', device=dict(pc_id='PC-01', online=True, exam_mode=mode(False))))
                send_message(peer, dict(type='DEVICE_LIST', devices=[]))
                self.wait_until(lambda: client.events.qsize() == 82)
                messages = [client.events.get_nowait()[1] for _ in range(82)]
                self.assertEqual([m['event_id'] for m in messages[:80]], [str(i) for i in range(80)])
                self.assertEqual(messages[80]['type'], 'STATUS_UPDATE')
                self.assertEqual(messages[81]['type'], 'DEVICE_LIST')
        finally:
            if client:
                client.close()
                client.worker.join(2)
                self.assertFalse(client.worker.is_alive())
            peer.close()

    def test_queue_overflow_is_reported_not_silent_event_replacement(self):
        client_socket, peer = socket.socketpair()
        client = None
        try:
            with patch('admin_gui.exam_events.connect', return_value=client_socket):
                client = ExamEventClient('localhost', 0, object(), 'test-admin', capacity=1)
                peer.settimeout(3)
                recv_message(peer)
                send_message(peer, dict(type='REGISTER_RESULT', ok=True))
                recv_message(peer)
                send_message(peer, detection('first'))
                send_message(peer, detection('overflow'))
                self.wait_until(lambda: client.events.qsize() == 1 and client.sock is None)
                generation, connected, label = client.connection.get(timeout=1)
                self.assertFalse(connected)
                self.assertIn('큐 초과', label)
                self.assertEqual(client.events.get_nowait()[1]['event_id'], 'first')
        finally:
            if client:
                client.close()
                client.worker.join(2)
            peer.close()

    def test_authentication_rejection_stops_without_commands_or_retry(self):
        client_socket, peer = socket.socketpair()
        client = None
        try:
            with patch('admin_gui.exam_events.connect', return_value=client_socket) as connect:
                client = ExamEventClient('localhost', 0, object(), 'test-admin')
                peer.settimeout(3)
                recv_message(peer)
                send_message(peer, dict(type='REGISTER_RESULT', ok=False))
                client.worker.join(2)
                self.assertFalse(client.worker.is_alive())
                self.assertEqual(connect.call_count, 1)
                self.assertFalse(client.connection.get_nowait()[1])
        finally:
            if client:
                client.close()
            peer.close()
