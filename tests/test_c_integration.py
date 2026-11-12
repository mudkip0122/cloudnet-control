import unittest
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from admin_gui.exam_window import exam_request
from admin_gui.process_control import batch_targets, command_request
from admin_gui.presentation import device_view
from admin_gui.process_window import ProcessWindow


class ContractTests(unittest.TestCase):
    def test_batch_filters_protected_and_invalid_identity(self):
        rows = [dict(pid=1, create_time=12.5, can_stop=True),
                dict(pid=2, create_time=1, can_stop=False),
                dict(pid=3, create_time=float('nan'), can_stop=True),
                dict(pid=4, create_time=0, can_stop=True)]
        self.assertEqual(batch_targets(rows + rows), [dict(pid=1, create_time=12.5)])

    def test_batch_limit_and_empty(self):
        for rows in ([], [dict(pid=i + 1, create_time=1, can_stop=True) for i in range(101)]):
            with self.assertRaises(ValueError):
                batch_targets(rows)

    def test_batch_request_freezes_identity(self):
        row = dict(pid=12, create_time=1.5, can_stop=True)
        request = command_request('PC-03', 'STOP_PROCESSES', [row])
        row['pid'] = 99
        self.assertEqual(request['targets'], [dict(pid=12, create_time=1.5)])
        self.assertNotEqual(request['command_id'], command_request('PC-03', 'LIST_PROCESSES')['command_id'])

    def test_exam_commands(self):
        request = exam_request('PC-03', 'on', 'exam-1', 'Discord.exe,discord.EXE', '0.5')
        self.assertEqual(request['forbidden_processes'], ['discord.exe'])
        self.assertTrue(request['enabled'])
        self.assertFalse(exam_request('PC-03', 'off', 'exam-1')['enabled'])
        self.assertEqual(exam_request('PC-03', 'status')['action'], 'GET_EXAM_STATUS')

    def test_exam_invalid_settings(self):
        for exam_id, names, interval in [('bad id', 'a.exe', '1'), ('ok', '*.exe', '1'),
                                         ('ok', '', '1'), ('ok', 'a.exe', 'nan'), ('ok', 'a.exe', '61')]:
            with self.assertRaises(ValueError):
                exam_request('PC-03', 'on', exam_id, names, interval)

    def test_rtt_freshness_uses_own_timestamp(self):
        now = datetime.now(timezone.utc)
        device = dict(pc_id='PC-03', pc_name='test', online=True, metrics_stale=False, metrics=dict(
            timestamp=now.isoformat(), rtt_timestamp=now.isoformat(), rtt_ms=24.185, rtt_status='ok'))
        self.assertEqual(device_view(device, now=now)['rtt'], '24.2 ms')
        device['metrics']['rtt_timestamp'] = (now - timedelta(seconds=9)).isoformat()
        self.assertEqual(device_view(device, now=now)['rtt'], '—')
        device['metrics']['rtt_status'] = 'timeout'
        self.assertEqual(device_view(device, now=now)['rtt'], '응답 지연')
        self.assertEqual(device_view(device, connected=False, now=now)['rtt'], '—')


class GroupWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = ProcessWindow(SimpleNamespace(root=self.root, context=object(), token='test'))
        self.window.set_target(dict(pc_id='PC-03', pc_name='test', online=True), True)
        self.submitted = []
        def submit(request):
            self.window.pending = request
            self.window.pending_generation = self.window.generation
            self.submitted.append(request)
        self.window.submit = submit
        self.window.rows = {
            '1': dict(pid=1, name='App.exe', username='u', memory_bytes=10, create_time=1, can_stop=True),
            '2': dict(pid=2, name='app.EXE', username='u', memory_bytes=20, create_time=2, can_stop=False),
            '3': dict(pid=3, name=None, username=None, memory_bytes=None, create_time=3, can_stop=False),
            '4': dict(pid=4, name=None, username=None, memory_bytes=None, create_time=4, can_stop=False)}
        self.window.complete = True
        self.window.grouped.set(True)
        self.window.render_rows()

    def tearDown(self):
        self.window.close()
        self.root.destroy()

    def test_group_confirmation_and_individual_results(self):
        window = self.window
        self.assertEqual(window.table.get_children('group:app.exe'), ('1', '2'))
        self.assertIn('3', window.table.get_children())
        self.assertIn('4', window.table.get_children())
        window.table.selection_set('group:app.exe', '1')
        with patch('admin_gui.process_window.messagebox.askokcancel', return_value=True) as confirm:
            window.stop_batch()
        self.assertIn('제외 보호 항목 1개', confirm.call_args.args[1])
        request = self.submitted[-1]
        self.assertEqual(request['targets'], [dict(pid=1, create_time=1)])
        window.handle_result(request, dict(ok=True, code='PROCESS_BATCH_RESULT', counts=dict(TERMINATION_REQUESTED=1),
            results=[dict(pid=1, create_time=1, ok=True, code='TERMINATION_REQUESTED')]))
        self.assertIn('TERMINATION_REQUESTED', window.batch_results.get('1.0', 'end'))
        self.assertEqual(self.submitted[-1]['action'], 'LIST_PROCESSES')

    def test_target_change_in_confirmation_cancels_batch(self):
        self.window.table.selection_set('group:app.exe')
        def change(*args, **kwargs):
            self.window.set_target(None, False)
            return True
        with patch('admin_gui.process_window.messagebox.askokcancel', side_effect=change):
            self.window.stop_batch()
        self.assertEqual(self.submitted, [])

    def test_search_name_pid_user_empty_and_clear(self):
        window = self.window
        window.search.set('APP.EXE')
        self.assertEqual(window.table.get_children('group:app.exe'), ('1', '2'))
        self.assertTrue(window.table.item('group:app.exe', 'open'))
        self.assertIn('표시 2 / 전체 4개', window.search_count.get())
        window.search.set('3')
        self.assertEqual(window.table.get_children(), ('3',))
        window.search.set('U')
        self.assertEqual(window.table.get_children('group:app.exe'), ('1', '2'))
        window.search.set('no-such-process')
        self.assertEqual(window.table.get_children(), ())
        self.assertIn('검색 결과 없음', window.search_count.get())
        self.assertTrue(window.batch_button.instate(['disabled']))
        window.search.set('')
        self.assertEqual(len(window.table.get_children()), 3)

    def test_filtered_group_batch_never_adds_hidden_process(self):
        window = self.window
        window.rows['5'] = dict(pid=5, name='APP.exe', username='hidden-user', memory_bytes=10,
                                create_time=5, can_stop=True)
        window.rows['1']['username'] = 'visible-user'
        window.search.set('visible-user')
        window.table.selection_set('group:app.exe')
        with patch('admin_gui.process_window.messagebox.askokcancel', return_value=True):
            window.stop_batch()
        self.assertEqual(self.submitted[-1]['targets'], [dict(pid=1, create_time=1)])

    def test_search_survives_refresh_and_reused_pid_loses_selection(self):
        window = self.window
        window.grouped.set(False)
        window.search.set('app')
        window.table.selection_set('1')
        window.refresh()
        request = self.submitted[-1]
        rows = [dict(row) for row in window.rows.values()]
        rows[0]['create_time'] = 99
        window.handle_result(request, dict(ok=True, code='PROCESS_LIST', processes=rows))
        self.assertEqual(window.search.get(), 'app')
        self.assertEqual(window.table.get_children(), ('1', '2'))
        self.assertEqual(window.table.selection(), ())
