"""TLS integration of B timeline events through A to C's parser."""
from datetime import datetime, timezone

from common.protocol import recv_message, send_message
from admin_gui.exam_events import ExamStore
from tests.test_server_exam_stream import ExamStreamTests


class ChromeTimelineTests(ExamStreamTests):
    def test_chrome_events_are_durable_deduplicated_and_relayed(self):
        agent, registered = self.connect('agent')
        self.assertTrue(registered.get('exam_chrome_timeline_v1'))
        subscriber, _ = self.connect(subscribe_exam=True)
        send_message(subscriber, {'type': 'LIST_DEVICES'})
        self.assertEqual(recv_message(subscriber)['type'], 'DEVICE_LIST')
        events = [
            {'kind': 'EXAM_TAB_CHANGED', 'transition': value}
            for value in ('left', 'returned', 'closed')
        ] + [
            {'kind': 'EXAM_CAMERA_STATUS', 'status': value}
            for value in ('on', 'off', 'permission_denied', 'unavailable')
        ] + [
            {'kind': 'EXAM_TAB_MONITOR_STATUS', 'status': 'monitoring', 'reason': 'extension_connected'},
            {'kind': 'EXAM_TAB_MONITOR_STATUS', 'status': 'unavailable', 'reason': 'event_buffer_overflow', 'dropped_count': 2},
        ]
        store = ExamStore()
        for index, fields in enumerate(events):
            event = dict(type='EXAM_EVENT', version=1, pc_id='PC-01', exam_id='exam-1',
                         event_id=f'chrome-{index}', timestamp=datetime.now(timezone.utc).isoformat(), **fields)
            send_message(agent, event)
            self.assertEqual(recv_message(subscriber), event)
            store.accept(event)
            send_message(agent, event)
        self.barrier(agent)
        count = self.audit.db.execute('SELECT count(*) FROM exam_events').fetchone()[0]
        self.assertEqual(count, len(events))
