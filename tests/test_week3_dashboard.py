"""Real Tk/Matplotlib checks with synthetic PC measurements; no external server."""
from datetime import datetime, timezone
import unittest
import queue
from types import SimpleNamespace
from unittest.mock import patch
import customtkinter as ctk

from admin_gui.main import Dashboard
from tests.test_gui_exam_events import detection, mode
from tests.test_gui_foreground import transition, foreground_status


class WeekThreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        with patch.object(Dashboard, 'receive'):
            self.dashboard = Dashboard(self.root, 0)
        self.now = datetime.now(timezone.utc)
        self.devices = [dict(pc_id=f'PC-0{i}', pc_name='same name', online=True,
            last_received=self.now.isoformat(), metrics=dict(timestamp=self.now.isoformat(),
                cpu_percent=i * 10, ram_percent=i * 20, upload_mbps=0, download_mbps=None)) for i in range(1, 4)]
        self.refresh(True, self.devices)

    def refresh(self, connected, data):
        self.dashboard.publish((connected, data))
        self.root.after_cancel(self.dashboard.timer)
        self.dashboard.tick()

    def tearDown(self):
        with patch.object(self.root, 'destroy'):
            self.dashboard.close()
        for child in list(self.root.winfo_children()):
            child.destroy()

    def test_pc_switch_displays_correct_usage_and_series(self):
        d = self.dashboard
        for i in (3, 1, 2, 3):
            d.table.selection_set(f'PC-0{i}')
            d.show_detail()
            self.assertEqual(d.usage_values['cpu'].get(), f'{i * 10:.1f} %')
            self.assertEqual(list(d.charts.lines['cpu_percent'].get_ydata()), [i * 10])
            self.assertEqual(list(d.charts.lines['ram_percent'].get_ydata()), [i * 20])
            self.assertEqual(d.charts.pc_id, f'PC-0{i}')
        self.assertNotEqual(d.details['gui_received'].get(), '—')

    def test_monitor_pc_order_is_fixed_across_arrivals_and_reconnects(self):
        d = self.dashboard
        self.refresh(True, [])
        self.refresh(True, list(reversed(self.devices)))
        self.assertEqual(d.table.get_children(), ('PC-01', 'PC-02', 'PC-03'))
        self.assertEqual(d.table.selection(), ('PC-01',))
        d.table.selection_set('PC-02')
        self.refresh(True, self.devices[1:])
        self.refresh(True, self.devices[1:] + self.devices[:1])
        self.assertEqual(d.table.get_children(), ('PC-01', 'PC-02', 'PC-03'))
        self.assertEqual(d.table.selection(), ('PC-02',))

    def test_exam_pc_buttons_route_exact_target_and_ignore_stale_results(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        with patch('admin_gui.exam_window.ProcessClient') as factory:
            client = factory.return_value
            client.worker.is_alive.return_value = False
            client.results = queue.Queue()
            d.open_exam()
        window = d.exam_window

        def deliver(request, result_mode, code='EXAM_STATUS'):
            client.results.put((request, dict(ok=True, code=code, exam_mode=result_mode)))
            window.window.after_cancel(window.timer)
            window.tick()

        initial = client.submit.call_args.args[0]
        self.assertEqual(initial['target_id'], 'PC-01')
        window.inputs[0].set('joint-trial')
        deliver(initial, mode(False))
        self.assertEqual(window.inputs[0].get(), 'joint-trial')
        d.table.selection_set('PC-03')
        d.show_detail()
        self.assertEqual(window.pc_choice.get(), 'PC-01')
        window.pc_controls['PC-02']['on'].invoke()
        enable = client.submit.call_args.args[0]
        self.assertEqual((enable['target_id'], enable['action'], enable['enabled']),
                         ('PC-02', 'SET_EXAM_MODE', True))
        self.assertNotIn('forbidden_processes', enable)
        self.assertTrue(all(button.instate(['disabled']) for button in window.buttons))
        window.pc_choice.set('PC-03')
        window.sync_devices()
        deliver(enable, dict(mode(), exam_id='joint-trial'), code='EXAM_MODE_ENABLED')
        query = client.submit.call_args.args[0]
        self.assertEqual((query['target_id'], query['action']), ('PC-03', 'GET_EXAM_STATUS'))
        deliver(query, mode(False))
        d.exam_store.status_update(dict(self.devices[2], exam_mode=dict(mode(), exam_id='pc03-current')))
        window.pc_controls['PC-03']['off'].invoke()
        disable = client.submit.call_args.args[0]
        self.assertEqual((disable['target_id'], disable['enabled'], disable['exam_id']),
                         ('PC-03', False, 'pc03-current'))
        mutations = [call.args[0] for call in client.submit.call_args_list if call.args[0]['action'] == 'SET_EXAM_MODE']
        self.assertEqual(len(mutations), 2)

    def test_exam_offline_pc_controls_disabled(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        self.devices[1]['online'] = False
        self.refresh(True, self.devices)
        with patch('admin_gui.exam_window.ProcessClient') as factory:
            client = factory.return_value
            client.worker.is_alive.return_value = False
            d.open_exam()
        window = d.exam_window
        self.assertTrue(all(window.pc_controls['PC-02'][action].instate(['disabled'])
                            for action in ('on', 'off', 'status')))
        count = client.submit.call_count
        window.submit_for_pc('PC-02', 'on')
        self.assertEqual(client.submit.call_count, count)
        self.refresh(False, 'synthetic disconnect')
        self.assertTrue(all(button.instate(['disabled']) for button in window.buttons))

    def test_disconnect_hides_current_values_preserves_history_and_disables_controls(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        self.refresh(True, self.devices)
        self.assertFalse(d.process_button.instate(['disabled']))
        self.refresh(False, 'synthetic disconnect')
        self.assertEqual(d.usage_values['cpu'].get(), '—')
        self.assertTrue(d.process_button.instate(['disabled']))
        self.assertTrue(all(button.instate(['disabled']) for button in d.preview_buttons))
        self.assertIn('이전 측정 기록', d.charts.notice.cget('text'))
        self.assertEqual(len(d.history.samples['PC-01']), 1)

    def test_repeated_updates_reuse_artists_and_limit_draw_rate(self):
        charts = self.dashboard.charts
        artists = tuple(charts.lines.values())
        points = [(self.now.timestamp(), dict(cpu_percent=10, ram_percent=20, upload_mbps=0, download_mbps=0))]
        with patch('admin_gui.charts.time.monotonic', return_value=100):
            charts.last_draw = 100
            with patch.object(charts, 'draw') as draw:
                for index in range(50):
                    points[0][1]['cpu_percent'] = index
                    charts.show(points, '온라인', 'PC-01')
                draw.assert_not_called()
                self.assertIsNotNone(charts.pending_draw)
        charts.draw()
        self.assertEqual(list(charts.lines['cpu_percent'].get_ydata()), [49])
        self.assertEqual(tuple(charts.lines.values()), artists)
        self.assertEqual([len(axis.lines) for axis in charts.axes], [2, 2])

    def test_exam_warning_history_dedup_state_sync_and_offline_viewing(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        d.exam_client = SimpleNamespace(events=queue.Queue(), connection=queue.Queue(), close=lambda: None,
                                       worker=SimpleNamespace(is_alive=lambda: False))
        d.exam_client.connection.put((1, True, 'test subscription'))
        self.devices[0]['exam_mode'] = mode()
        d.exam_client.events.put((1, dict(type='DEVICE_LIST', devices=self.devices)))
        event = detection()
        d.exam_client.events.put((1, event))
        d.exam_client.events.put((1, event))
        d.drain_exam_events()
        self.assertEqual(len(d.exam_store.history), 1)
        self.assertIn('discord.exe', d.exam_warning.get())
        self.assertIn('현재 실행 여부 미확인', d.exam_warning.get())
        with patch('admin_gui.exam_window.ProcessClient') as client:
            client.return_value.worker.is_alive.return_value = False
            d.open_exam()
        window = d.exam_window
        self.assertEqual(len(window.history_table.get_children()), 1)
        self.assertIn('검사 시작 중', window.monitor_text.get())
        d.exam_client.events.put((1, dict(type='STATUS_UPDATE', device=dict(self.devices[0], exam_mode=mode(False)))))
        d.drain_exam_events()
        window.refresh_history()
        self.assertIn('꺼짐', window.monitor_text.get())
        self.refresh(False, 'synthetic disconnect')
        self.assertIn('연결 끊김', window.monitor_text.get())
        self.assertEqual(len(window.history_table.get_children()), 1)
        self.assertFalse(d.exam_button.instate(['disabled']))

    def test_chrome_transition_history_and_foreground_gap_in_widgets(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        d.exam_client = SimpleNamespace(events=queue.Queue(), connection=queue.Queue(), close=lambda: None,
                                       worker=SimpleNamespace(is_alive=lambda: False))
        self.devices[0]['exam_mode'] = mode()
        d.exam_store.snapshot(self.devices)
        d.exam_client.connection.put((1, True, 'test subscription'))
        for event in (foreground_status(), transition(), transition(), foreground_status('gap', 'unavailable')):
            d.exam_client.events.put((1, event))
        d.drain_exam_events()
        self.assertIn('Chrome 이탈', d.foreground_warning.get())
        self.assertIn('notepad.exe', d.foreground_warning.get())
        self.assertNotIn('Chrome 이탈', d.exam_warning.get())
        with patch('admin_gui.exam_window.ProcessClient') as client:
            client.return_value.worker.is_alive.return_value = False
            d.open_exam()
        window = d.exam_window
        self.assertEqual(len(window.history_table.get_children()), 3)
        self.assertIn('감시 공백', window.foreground_text.get())
        d.exam_client.events.put((1, transition('return', 'returned')))
        d.drain_exam_events()
        window.refresh_history()
        self.assertIn('Chrome 복귀', d.foreground_warning.get())
        self.assertEqual(len(window.history_table.get_children()), 4)

    def test_newest_history_first_and_delete_selected_stable_event_identity(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        d.exam_store.accept(detection('old'))
        d.exam_store.accept(detection('new'))
        with patch('admin_gui.exam_window.ProcessClient') as client:
            client.return_value.worker.is_alive.return_value = False
            d.open_exam()
        window = d.exam_window
        self.assertEqual(window.history_table.get_children(), ('new', 'old'))
        window.history_table.selection_set('old')
        d.exam_store.accept(detection('latest'))
        window.refresh_history()
        self.assertEqual(window.history_table.get_children(), ('latest', 'new', 'old'))
        self.assertEqual(window.history_table.selection(), ('old',))
        window.delete_selected_history()
        self.assertEqual(window.history_table.get_children(), ('latest', 'new'))
        self.assertFalse(d.exam_store.accept(detection('old')))

    def test_clear_history_cancel_new_arrival_and_banner_reset(self):
        d = self.dashboard
        d.context, d.token = object(), 'synthetic'
        d.exam_store.accept(detection('before'))
        d.exam_store.accept(transition('transition'))
        d.update_exam_warnings()
        with patch('admin_gui.exam_window.ProcessClient') as client:
            client.return_value.worker.is_alive.return_value = False
            d.open_exam()
        window = d.exam_window
        with patch('admin_gui.exam_window.messagebox.askokcancel', return_value=False):
            window.delete_all_history()
        self.assertEqual(len(d.exam_store.history), 2)
        def arrive(*args, **kwargs):
            d.exam_store.accept(detection('during-confirmation'))
            window.refresh_history()
            return True
        with patch('admin_gui.exam_window.messagebox.askokcancel', side_effect=arrive):
            window.delete_all_history()
        self.assertEqual(window.history_table.get_children(), ('during-confirmation',))
        self.assertIsNone(d.exam_store.latest_foreground)
        with patch('admin_gui.exam_window.messagebox.askokcancel', return_value=True):
            window.delete_all_history()
        self.assertEqual(window.history_table.get_children(), ())
        self.assertTrue(window.delete_all_button.instate(['disabled']))
        self.assertIsNone(d.exam_store.latest_warning)
        self.assertIn('표시할 금지 프로그램 감지 이력 없음', d.exam_warning.get())
        self.assertIn('표시할 Chrome 이탈·복귀 이력 없음', d.foreground_warning.get())
