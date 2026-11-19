from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from admin_gui.alerts import AlertWindow
from admin_gui.history_query import collect_audit_history, history_request, query_audit_history, sample_history
from admin_gui.history_window import HistoryWindow
from admin_gui.theme import configure_theme


class AuditHistoryClientTests(unittest.TestCase):
    def setUp(self):
        self.request = dict(history_request("PC-03", 10), type="GET_AUDIT_HISTORY", kind="power")
        self.result = {key: self.request[key] for key in ("version", "request_id", "pc_id", "kind")}
        self.result.update(type="AUDIT_HISTORY_RESULT", ok=True)
        self.stamp = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        self.row = dict(pc_id="PC-03", command_id="command", action="REBOOT_PC",
                        power_control_mode="simulate", requested_at=self.stamp, outcome="result_received",
                        result_code="POWER_SIMULATED", result_received_at=self.stamp, reboot_confirmed_at=None,
                        stages=[dict(stage="partial_result", code="POWER_ACCEPTED", occurred_at=self.stamp)])

    def test_pages_and_legacy_record_without_stages(self):
        pages = iter((dict(self.result, page=0, done=False, rows=[self.row]),
                      dict(self.result, page=1, done=True,
                           rows=[dict(self.row, command_id="legacy", stages=[])])))
        first, second = collect_audit_history(self.request, lambda: next(pages))
        self.assertEqual(first["stages"][0][2], "POWER_ACCEPTED")
        self.assertEqual(second["result_code"], "POWER_SIMULATED")
        self.assertEqual(second["stages"], [])

    def test_custom_range_converts_local_time_and_validates_limits(self):
        start = "2026-11-19 16:20"
        end = "2026-11-19 16:21"
        request = history_request("sample-pc-01", start_at=start, end_at=end)
        expected = datetime(2026, 11, 19, 16, 20).astimezone(timezone.utc)
        self.assertEqual(datetime.fromisoformat(request["start_at"]), expected)
        self.assertEqual(datetime.fromisoformat(request["end_at"]), expected + timedelta(minutes=1))
        points, _ = sample_history(request)
        self.assertTrue(points)
        for invalid_end, hint in ((start, "늦어야"), ("2026-12-20 16:20", "최대 30일"), ("invalid", "형식")):
            with self.assertRaisesRegex(ValueError, hint):
                history_request("PC-03", start_at=start, end_at=invalid_end)

    def test_wrong_pc_and_wrong_audit_kind_are_rejected(self):
        for reply in (dict(self.result, kind="exam", page=0, done=True, rows=[]),
                      dict(self.result, page=0, done=True, rows=[dict(self.row, pc_id="PC-02")])):
            with self.assertRaises(ValueError):
                collect_audit_history(self.request, lambda: reply)

    def test_unsupported_server_only_receives_registration(self):
        with patch("admin_gui.history_query.connect", return_value=MagicMock()), \
                patch("admin_gui.history_query.recv_message", return_value=dict(type="REGISTER_RESULT", ok=True)), \
                patch("admin_gui.history_query.send_message") as send:
            with self.assertRaisesRegex(ValueError, "지원하지 않습니다"):
                query_audit_history("localhost", 0, object(), "synthetic", None, self.request)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1]["type"], "REGISTER")

    def test_history_tabs_render_only_the_selected_request(self):
        root = tk.Tk()
        root.withdraw()
        dashboard = SimpleNamespace(root=root, host="localhost", port=0, context=object(), token="synthetic",
                                    server_name=None, sample=False, devices={"PC-03": {}})
        history = HistoryWindow(dashboard, None)
        old, new = Future(), Future()
        old.set_running_or_notify_cancel()
        try:
            history.pc.set("PC-03")
            history.range.current(2)
            history.range_changed()
            history.start_at.set("2026-11-19 16:00")
            history.end_at.set("2026-11-19 16:30")
            with patch.object(history.executor, "submit", side_effect=(old, new)) as submit:
                history.notebook.select(history.exam.master)
                history.tab_changed()
                history.notebook.select(history.power.master)
                history.tab_changed()
            self.assertEqual([call.args[-1]["kind"] for call in submit.call_args_list], ["exam", "power"])
            for call in submit.call_args_list:
                request = call.args[-1]
                self.assertEqual(datetime.fromisoformat(request["end_at"]) - datetime.fromisoformat(request["start_at"]),
                                 timedelta(minutes=30))
            old.set_result([])
            history.window.after_cancel(history.timer)
            history.tick()
            self.assertEqual(history.status.get(), "조회 중…")

            reply = dict(self.result, page=0, done=True, rows=[self.row])
            new.set_result(collect_audit_history(self.request, lambda: reply))
            history.window.after_cancel(history.timer)
            history.tick()
            (item,) = history.power.get_children()
            self.assertIn("모의 실행 완료", history.power.item(item, "values"))
            self.assertFalse(history.exam.get_children())
            history.power.selection_set(item)
            history.show_power_detail()
            self.assertIn("POWER_ACCEPTED", history.power_detail.cget("text"))
            history.search.set("pc-03 power_accepted")
            self.assertEqual(history.power.get_children(), (item,))
            self.assertEqual(history.search_count.get(), "1 / 1건")
            history.search.set("no match")
            self.assertFalse(history.power.get_children())
            self.assertFalse(history.power.selection())
            history.search.set("")
            self.assertEqual(history.power.get_children(), (item,))
        finally:
            history.close()
            root.destroy()

    def test_tab_hit_areas_stay_fixed_for_every_selection(self):
        root = tk.Tk()
        root.withdraw()
        configure_theme(root)
        dashboard = SimpleNamespace(root=root, host="localhost", port=0, context=None, token=None,
                                    server_name=None, sample=True, devices={})
        history = HistoryWindow(dashboard, None)
        live_alerts = AlertWindow(root)

        def hit_areas(notebook):
            result = []
            for x in range(0, 520, 2):
                try:
                    result.append(notebook.index(f"@{x},14"))
                except tk.TclError:
                    result.append(None)
            return result

        try:
            root.update()
            for notebook, count in ((history.notebook, 4), (live_alerts.open_table.master.master, 2)):
                original = hit_areas(notebook)
                self.assertEqual(set(original) - {None}, set(range(count)))
                y = notebook.winfo_y()
                for index in (*range(1, count), 0):
                    notebook.select(index)
                    root.update()
                    self.assertEqual(hit_areas(notebook), original)
                    self.assertEqual(notebook.winfo_y(), y)
        finally:
            history.close()
            root.destroy()
