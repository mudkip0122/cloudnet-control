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
        window.handle_result(request, {"ok": True, "code": "PROCESS_LIST", "processes": [
            row(10), row(11, can_stop=False, protected_reason="PROTECTED_NAME"),
            row(12, can_stop=False, create_time=None, name=None, username=None, memory_bytes=None,
                protected_reason="ACCESS_DENIED_OR_EXITED")]})
        assert len(window.table.get_children()) == 3
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
        assert len(submitted) == 1
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
        window.handle_result(refresh, {"ok": False, "code": "TRANSPORT_ERROR"})
        assert not window.complete and str(window.stop_button["state"]) == "disabled"
        window.refresh()
        stale = submitted[-1]
        window.set_target(device | {"pc_id": "other"}, True)
        window.set_target(device, True)
        window.handle_result(stale, {"ok": True, "code": "PROCESS_LIST", "processes": [row()]})
        assert not window.complete and not window.table.get_children()
        print("PASS: protected disabled, unknown fields, confirmation/cancel, PID+create_time, result refresh, stale response blocked")
    finally:
        window.close()
        root.destroy()


if __name__ == "__main__":
    main()
