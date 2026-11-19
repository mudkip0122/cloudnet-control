from datetime import datetime, timedelta, timezone

from common.protocol import recv_message, send_message
from tests import test_server_exam_stream as fixtures


class AuditHistoryTests(fixtures.ExamStreamTests):
    def test_tls_reads_exam_and_power_for_selected_pc(self):
        now = datetime.now(timezone.utc)
        event = self.event()
        self.audit.event(event, now.isoformat())
        self.audit.event(dict(event, pc_id="PC-02", event_id="other"), now.isoformat())
        request = dict(type="COMMAND", target_id="PC-01", command_id="power-test",
                       action="REBOOT_PC", power_control_mode="simulate")
        self.audit.reserve(request, "internal", now.isoformat())
        self.audit.result("power-test", dict(code="POWER_ACCEPTED", terminal=False), complete=False)
        self.audit.result("power-test", dict(code="POWER_SIMULATED", terminal=True))
        self.audit.reserve(dict(request, command_id="list", action="LIST_PROCESSES"), "internal", now.isoformat())
        admin, registered = self.connect()
        self.assertTrue(registered["audit_history_v1"])
        query = dict(type="GET_AUDIT_HISTORY", version=1, request_id="test", pc_id="PC-01",
                     start_at=(now - timedelta(seconds=5)).isoformat(),
                     end_at=(now + timedelta(seconds=5)).isoformat())
        send_message(admin, dict(query, kind="exam"))
        reply = recv_message(admin)
        self.assertTrue(reply["done"])
        self.assertEqual([row["event"] for row in reply["rows"]], [event])
        send_message(admin, dict(query, kind="power"))
        row, = recv_message(admin)["rows"]
        self.assertEqual(row["command_id"], "power-test")
        self.assertEqual([item["code"] for item in row["stages"]],
                         [None, "POWER_ACCEPTED", "POWER_SIMULATED"])
        self.assertNotIn("executor", row)
        send_message(admin, dict(query, kind="power", start_at=query["end_at"]))
        self.assertEqual(recv_message(admin)["code"], "INVALID_REQUEST")
