from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from admin_gui.history_query import collect_audit_history, history_request, query_audit_history
from admin_gui.history_window import HistoryWindow


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
            with patch.object(history.executor, "submit", side_effect=(old, new)) as submit:
                history.notebook.select(history.exam.master)
                history.tab_changed()
                history.notebook.select(history.power.master)
                history.tab_changed()
            self.assertEqual([call.args[-1]["kind"] for call in submit.call_args_list], ["exam", "power"])
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
        finally:
            history.close()
            root.destroy()
