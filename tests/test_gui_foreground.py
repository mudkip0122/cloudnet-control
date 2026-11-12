"""B foreground event contract and C gap/history semantics, without window spying."""
from copy import deepcopy
import unittest

from admin_gui.exam_events import ExamStore, exam_event, event_columns
from tests.test_gui_exam_events import detection, mode


def transition(event_id='left', direction='left'):
    event = detection(event_id)
    chrome = dict(pid=100, name='Chrome.EXE', create_time=1000)
    other = dict(pid=200, name='notepad.exe', create_time=2000)
    event.update(kind='EXAM_WINDOW_CHANGED', transition=direction,
        previous_process=chrome if direction == 'left' else other,
        process=other if direction == 'left' else chrome)
    return event


def foreground_status(event_id='status', status='ok'):
    event = detection(event_id)
    event.pop('process')
    event.update(kind='FOREGROUND_MONITOR_STATUS', status=status)
    return event


class ForegroundGuiTests(unittest.TestCase):
    def setUp(self):
        self.store = ExamStore()
        self.store.foreground_stream_connected = True
        self.device = dict(pc_id='PC-01', online=True, agent_session_id='session-1', exam_mode=mode())
        self.store.snapshot([self.device])

    def test_initial_observation_does_not_invent_exit(self):
        self.assertIn('미확인', self.store.foreground_state('PC-01', True))
        self.store.accept(foreground_status())
        self.assertIsNone(self.store.latest_foreground)
        self.assertEqual(len(self.store.history), 1)
        self.assertIn('조회 가능', self.store.foreground_state('PC-01', True))

    def test_left_returned_dedup_and_process_warning_are_separate(self):
        left, returned = transition(), transition('return', 'returned')
        self.assertTrue(self.store.accept(left))
        self.assertFalse(self.store.accept(left))
        self.assertIsNone(self.store.latest_warning)
        self.assertEqual(event_columns(left), ('Chrome 이탈', 'Chrome.EXE → notepad.exe', '100 → 200'))
        self.store.accept(detection('forbidden'))
        self.store.accept(returned)
        self.assertEqual(self.store.latest_warning['event_id'], 'forbidden')
        self.assertEqual(self.store.latest_foreground['transition'], 'returned')
        self.assertEqual(event_columns(returned)[0], 'Chrome 복귀')
        self.assertEqual(len(self.store.history), 3)

    def test_gap_recovery_never_calculates_cross_gap_duration(self):
        self.store.accept(transition())
        self.store.accept(foreground_status('gap', 'unavailable'))
        self.assertIn('감시 공백', self.store.foreground_state('PC-01', True))
        self.store.accept(foreground_status('recovery'))
        self.assertEqual(self.store.latest_foreground['transition'], 'left')
        self.assertEqual([event['kind'] for event in self.store.history],
                         ['EXAM_WINDOW_CHANGED', 'FOREGROUND_MONITOR_STATUS', 'FOREGROUND_MONITOR_STATUS'])
        self.assertTrue(all('duration' not in event for event in self.store.history))

    def test_session_reset_offline_and_mode_off_clear_current_state_keep_history(self):
        self.store.accept(transition())
        self.store.status_update(dict(self.device, agent_session_id='session-2'))
        self.assertIn('미확인', self.store.foreground_state('PC-01', True))
        self.assertEqual(len(self.store.history), 1)
        self.store.status_update(dict(self.device, exam_mode=mode(False)))
        self.assertIn('감시 중지', self.store.foreground_state('PC-01', True))
        self.assertIn('감시 공백', self.store.foreground_state('PC-01', False))
        self.store.invalidate()
        self.assertEqual(len(self.store.history), 1)
        self.assertEqual(self.store.latest_foreground['transition'], 'left')

    def test_late_or_other_exam_event_is_history_only(self):
        self.store.accept(transition(), apply_current=False)
        self.assertEqual(self.store.foreground, {})
        other = transition('old-exam')
        other['exam_id'] = 'old-exam'
        self.store.accept(other)
        self.assertEqual(self.store.foreground, {})
        self.assertEqual(len(self.store.history), 2)

    def test_process_monitor_gap_does_not_override_foreground_monitor(self):
        self.store.accept(foreground_status())
        event = detection('process-gap')
        event.pop('process')
        event.update(kind='MONITOR_STATUS', status='unavailable', unreadable_count=2, poll_interval=1)
        self.store.accept(event)
        self.assertIn('조회 가능', self.store.foreground_state('PC-01', True))
        self.assertEqual(self.store.monitor['PC-01']['status'], 'unavailable')

    def test_invalid_direction_status_and_identity_are_rejected(self):
        bad = transition()
        bad['transition'] = 'returned'
        missing = transition()
        missing.pop('previous_process')
        reused = transition()
        reused['previous_process']['create_time'] = float('nan')
        same = transition()
        same['process'] = deepcopy(same['previous_process'])
        for event in (bad, missing, reused, same, foreground_status(status='partial')):
            with self.assertRaises(ValueError):
                exam_event(event)
