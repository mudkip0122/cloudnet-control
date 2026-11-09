"""GUI protection, confirmation, stale target and result-refresh verification."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tkinter as tk

from admin_gui.process_window import ProcessWindow
from tests.test_gui_processes import row


def main():
    root = tk.Tk()
    root.withdraw()
    dashboard = SimpleNamespace(root=root, host="localhost", port=0, context=object(), token="test")
    window = ProcessWindow(dashboard)
    submitted = []
    window.client = SimpleNamespace(submit=submitted.append, close=lambda: None,
                                    worker=SimpleNamespace(is_alive=lambda: False))
    device = {"pc_id": "pc", "pc_name": "Test PC", "online": True}
    try:
        window.set_target(device, True)
        window.refresh()
        request = submitted[-1]
        assert not window.table.get_children() and not window.complete
        rows = [
            row(10), row(11, can_stop=False, protected_reason="PROTECTED_NAME"),
            row(12, can_stop=False, create_time=None, name=None, username=None, memory_bytes=None,
                protected_reason="ACCESS_DENIED_OR_EXITED"),
            row(2, name="alpha.exe", memory_bytes=10 * 1048576),
            row(100, name="Beta.exe", memory_bytes=2 * 1048576)]
        window.handle_result(request, {"ok": True, "code": "PROCESS_LIST", "processes": rows})
        assert window.table.get_children() == ("2", "10", "11", "12", "100")
        window.table.selection_set("10")
        # Invoke the actual heading callback, not just the sorting helper.
        root.tk.call(window.table.heading("memory", "command"))
        assert window.table.get_children() == ("10", "11", "100", "2", "12")
        root.tk.call(window.table.heading("memory", "command"))
        assert window.table.get_children() == ("2", "100", "10", "11", "12")
        assert window.table.selection() == ("10",)
        window.sort_by("name")
        assert window.table.get_children() == ("2", "100", "10", "11", "12")
        window.sort_by("name")
        assert window.table.get_children() == ("10", "11", "100", "2", "12")
        window.sort_by("username")
        assert window.table.get_children()[-1] == "12"
        window.sort_by("can_stop")
        assert window.table.get_children() == ("11", "12", "2", "10", "100")
        window.sort_by("can_stop")
        assert window.table.get_children() == ("2", "10", "100", "11", "12")
        window.sort_by("protected_reason")
        assert window.table.get_children()[:2] == ("11", "12")
        window.sort_by("pid")
        assert window.table.get_children() == ("2", "10", "11", "12", "100")
        window.sort_by("memory")
        window.refresh()
        window.handle_result(submitted[-1], {"ok": True, "code": "PROCESS_LIST", "processes": list(reversed(rows))})
        assert window.table.get_children() == ("10", "11", "100", "2", "12")
        assert window.table.selection() == ("10",)
        assert "확인 불가" in window.table.item("12", "values")
        window.table.selection_set("11")
        window.update_buttons()
        assert str(window.stop_button["state"]) == "disabled"
        with patch("admin_gui.process_window.messagebox.askokcancel") as confirm:
            window.stop_selected()
            confirm.assert_not_called()
        window.table.selection_set("10")
        window.update_buttons()
        assert str(window.stop_button["state"]) == "normal"
        with patch("admin_gui.process_window.messagebox.askokcancel", return_value=False):
            window.stop_selected()
        assert len(submitted) == 2
        with patch("admin_gui.process_window.messagebox.askokcancel", return_value=True) as confirm:
            window.stop_selected()
            assert "PID: 10" in confirm.call_args.args[1]
            assert "Test PC" in confirm.call_args.args[1]
        stop = submitted[-1]
        assert stop["pid"] == 10 and stop["create_time"] == 1234.125
        assert not window.complete
        for code in ("TERMINATED",):
            window.handle_result(stop, {"ok": True, "code": code})
        assert submitted[-1]["action"] == "LIST_PROCESSES"
        assert not window.complete
        refresh = submitted[-1]
        window.handle_result(refresh, {"ok": True, "code": "PROCESS_LIST",
                                       "processes": [item for item in rows if item["pid"] != 10]})
        assert "PID 10" in window.outcome.get()
        assert "프로세스가 목록에 없습니다" in window.outcome.get()
        assert "다른 프로세스나 창은 남아" in window.outcome.get()
        # An acknowledged request is not the same as confirmed termination.
        window.last_stop = {"request": stop, "code": "TERMINATION_REQUESTED", "ok": True}
        window.rows["10"] = row(10)
        window.show_stop_observation()
        assert "종료 완료는 확인되지 않았습니다" in window.outcome.get()
        assert "아직 목록에 있습니다" in window.outcome.get()
        window.refresh()
        window.handle_result(submitted[-1], {"ok": False, "code": "TRANSPORT_ERROR"})
        assert not window.complete and str(window.stop_button["state"]) == "disabled"
        window.refresh()
        stale = submitted[-1]
        window.set_target(device | {"pc_id": "other"}, True)
        window.set_target(device, True)
        window.handle_result(stale, {"ok": True, "code": "PROCESS_LIST", "processes": [row()]})
        assert not window.complete and not window.table.get_children()
        print("PASS: six-column sorting, numeric values, missing last, sort/selection persistence, protected disabled, exact-PID results and refresh")
    finally:
        window.close()
        root.destroy()


if __name__ == "__main__":
    main()
