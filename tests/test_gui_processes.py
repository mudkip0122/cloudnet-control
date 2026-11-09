import unittest
import socket
import threading
from unittest.mock import patch

from admin_gui.process_control import ProcessClient, ProcessPages, ProcessProtocolError, command_request
from common.protocol import recv_message, send_message


def row(pid=10, **fields):
    return dict(pid=pid, name="test.exe", username="user", memory_bytes=1048576,
                create_time=1234.125, can_stop=True, protected_reason=None) | fields


def page(index, pages=2, rows=None, **fields):
    return dict(type="COMMAND_RESULT", command_id="list", pc_id="pc", ok=True,
                code="PROCESS_LIST", page=index, pages=pages,
                processes=rows if rows is not None else [row(10 + index)]) | fields


class ProcessGuiProtocolTests(unittest.TestCase):
    def test_all_pages_required_and_ordered(self):
        pages = ProcessPages("list", "pc")
        self.assertIsNone(pages.add(page(1)))
        self.assertEqual([item["pid"] for item in pages.add(page(0))], [10, 11])

    def test_empty_inventory_is_complete(self):
        self.assertEqual(ProcessPages("list", "pc").add(page(0, pages=1, rows=[])), [])

    def test_system_zero_creation_time_can_be_displayed_but_not_stopped(self):
        system = row(0, create_time=0.0, can_stop=False, protected_reason="AGENT_OR_SYSTEM_PID")
        self.assertEqual(ProcessPages("list", "pc").add(page(0, pages=1, rows=[system])), [system])
        with self.assertRaises(ProcessProtocolError):
            ProcessPages("list", "pc").add(page(0, pages=1, rows=[row(create_time=0.0)]))

    def test_mixed_identity_changed_count_and_duplicate_page_rejected(self):
        for bad in (page(1, pc_id="other"), page(1, command_id="other"),
                    page(1, pages=3), page(0), page(True), page(1, pages=0)):
            with self.subTest(bad=bad):
                assembly = ProcessPages("list", "pc")
                assembly.add(page(0))
                with self.assertRaises(ProcessProtocolError):
                    assembly.add(bad)

    def test_duplicate_pid_and_unusable_identity_rejected(self):
        invalid = [row(create_time=None), row(can_stop="false"), row(memory_bytes=-1),
                   row(create_time=float("nan")), row(protected_reason="PROTECTED")]
        for item in invalid:
            with self.subTest(row=item), self.assertRaises(ProcessProtocolError):
                ProcessPages("list", "pc").add(page(0, pages=1, rows=[item]))
        assembly = ProcessPages("list", "pc")
        assembly.add(page(0))
        with self.assertRaises(ProcessProtocolError):
            assembly.add(page(1, rows=[row(10)]))

    def test_stop_contains_exact_identity_and_unique_command(self):
        identity = row()
        first = command_request("pc", "STOP_PROCESS", identity)
        second = command_request("pc", "STOP_PROCESS", identity)
        self.assertEqual(first["pid"], identity["pid"])
        self.assertEqual(first["create_time"], identity["create_time"])
        self.assertNotEqual(first["command_id"], second["command_id"])
        with self.assertRaises(ValueError):
            command_request("pc", "STOP_PROCESS", row(can_stop=False))


class ProcessTransportTests(unittest.TestCase):
    def exchange(self, messages):
        client, peer = socket.socketpair()
        request = command_request("pc", "LIST_PROCESSES")
        errors = []

        def serve():
            with peer:
                try:
                    self.assertEqual(recv_message(peer)["role"], "admin")
                    send_message(peer, {"type": "REGISTER_RESULT", "ok": True})
                    self.assertEqual(recv_message(peer), request)
                    for message in messages:
                        send_message(peer, message | ({"command_id": request["command_id"]}
                            if message.get("command_id") == "list" else {}))
                except BaseException as exc:
                    errors.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
        transport = ProcessClient("localhost", 0, object(), "test-token", timeout=1)
        try:
            with patch("admin_gui.process_control.connect", return_value=client):
                return transport.execute(request)
        finally:
            transport.close()
            transport.worker.join(2)
            worker.join(2)
            if errors:
                raise errors[0]

    def test_interleaved_traffic_and_out_of_order_pages(self):
        result = self.exchange([{"type": "METRICS"}, page(0, command_id="unrelated"), page(1), page(0)])
        self.assertEqual([item["pid"] for item in result["processes"]], [10, 11])

    def test_disconnect_after_partial_inventory_never_returns_rows(self):
        with self.assertRaises(EOFError):
            self.exchange([page(0)])

    def test_wrong_pc_result_is_rejected(self):
        with self.assertRaises(ProcessProtocolError):
            self.exchange([page(0, pages=1, pc_id="other")])


if __name__ == "__main__":
    unittest.main()
