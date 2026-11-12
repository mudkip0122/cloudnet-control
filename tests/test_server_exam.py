"""A contract/storage tests for B 3a091ef; no changes to B or C code."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock

from common.protocol import recv_message, send_message
from server.audit import AuditStore
from server.contracts import command_error, command_result, exam_event, rtt_fields
from server.main import MonitoringServer, Session
from tests import test_server_week3 as week3

metric = week3.metric


def request(action="GET_EXAM_STATUS", **fields):
    return dict(type="COMMAND", command_id="unique-command", target_id="pc", action=action,
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()) | fields


def event(**changes):
    return dict(type="EXAM_EVENT", version=1, event_id="unique-event", pc_id="pc", exam_id="exam-1",
                timestamp=datetime.now(timezone.utc).isoformat(), kind="MONITOR_STATUS",
                status="partial", unreadable_count=2, poll_interval=1) | changes


class ContractTests(unittest.TestCase):
    def test_valid_and_invalid_batch_targets(self):
        good = request("STOP_PROCESSES", targets=[dict(pid=12, create_time=1.25)])
        self.assertIsNone(command_error(good))
        for targets in ([], [dict(pid=True, create_time=1)], [dict(pid=1, create_time=0)],
                        [dict(pid=1, create_time=float("inf"))], good["targets"] * 2,
                        [dict(pid=i + 1, create_time=1) for i in range(101)]):
            self.assertEqual(command_error(good | dict(targets=targets)), "INVALID_TARGETS")

    def test_exam_settings_match_b_contract(self):
        good = request("SET_EXAM_MODE", enabled=True, exam_id="exam-1",
                       forbidden_processes=["Discord.exe"], poll_interval=.5)
        self.assertIsNone(command_error(good))
        for changes in (dict(enabled=1), dict(exam_id="bad id"), dict(forbidden_processes=[]),
                        dict(forbidden_processes=["*.exe"]), dict(poll_interval=True),
                        dict(poll_interval=10 ** 400), dict(poll_interval=61)):
            self.assertEqual(command_error(good | changes), "INVALID_EXAM_SETTINGS")
        self.assertIsNone(command_error(request("SET_EXAM_MODE", enabled=False, exam_id="exam-1")))

    def test_omitted_policy_delegates_to_agent_but_null_and_empty_are_rejected(self):
        omitted = request("SET_EXAM_MODE", enabled=True, exam_id="exam-default")
        self.assertIsNone(command_error(omitted))
        self.assertNotIn("forbidden_processes", omitted)
        for value in (None, [], "discord.exe", ["*.exe"]):
            self.assertEqual(command_error(omitted | dict(forbidden_processes=value)), "INVALID_EXAM_SETTINGS")
        self.assertEqual(command_error(omitted | dict(poll_interval=0)), "INVALID_EXAM_SETTINGS")

    def test_enabled_result_must_include_resolved_policy(self):
        req = request("SET_EXAM_MODE", enabled=True, exam_id="exam-default")
        with self.assertRaises(ValueError):
            command_result(req, dict(ok=True, code="EXAM_MODE_ENABLED", exam_mode=dict(
                enabled=True, exam_id="exam-default", poll_interval=1, monitor_status="starting")))

    def test_missing_exam_policy_is_allowed_but_explicit_empty_or_null_is_not(self):
        default = request('SET_EXAM_MODE', enabled=True, exam_id='exam-1')
        self.assertIsNone(command_error(default))
        for invalid in ([], None, '', ['*.exe']):
            self.assertEqual(command_error(default | dict(forbidden_processes=invalid)), 'INVALID_EXAM_SETTINGS')
        self.assertEqual(command_error(default | dict(poll_interval=0)), 'INVALID_EXAM_SETTINGS')

    def test_enabled_result_must_still_include_the_resolved_policy(self):
        req = request('SET_EXAM_MODE', enabled=True, exam_id='exam-1')
        mode = dict(enabled=True, exam_id='exam-1', poll_interval=1, monitor_status='starting')
        response = dict(ok=True, code='EXAM_MODE_ENABLED', exam_mode=mode)
        with self.assertRaises(ValueError):
            command_result(req, response)
        mode['forbidden_processes'] = ['discord.exe']
        command_result(req, response)

    def test_rtt_compatibility_and_invalid_combinations(self):
        self.assertEqual(rtt_fields(metric()), {})
        stamp = datetime.now(timezone.utc).isoformat()
        for status in ("pending", "timeout", "stale"):
            self.assertEqual(rtt_fields(dict(rtt_status=status, rtt_ms=None, rtt_timestamp=None))["rtt_status"], status)
        good = dict(rtt_status="ok", rtt_ms=24.2, rtt_timestamp=stamp)
        self.assertEqual(rtt_fields(good), good)
        for changes in (dict(rtt_status="bad"), dict(rtt_ms=-1), dict(rtt_ms=True),
                        dict(rtt_ms=float("nan")), dict(rtt_timestamp=None), dict(rtt_timestamp="bad"),
                        dict(rtt_status="timeout")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rtt_fields(good | changes)
        with self.assertRaises(ValueError):
            rtt_fields(dict(rtt_ms=2))

    def test_event_validation(self):
        self.assertEqual(exam_event(event(), "pc")["unreadable_count"], 2)
        detected = event(kind="FORBIDDEN_PROCESS_DETECTED", process=dict(pid=12, name="demo.exe", create_time=1))
        self.assertEqual(exam_event(detected, "pc")["process"]["pid"], 12)
        for changes in (dict(pc_id="other"), dict(version=True), dict(version=2), dict(event_id=""),
                        dict(kind="UNKNOWN"), dict(exam_id="bad id"), dict(timestamp="2026-11-12"),
                        dict(unreadable_count=-1), dict(poll_interval=0), dict(status="off")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                exam_event(event(**changes), "pc")

    def test_batch_summary_and_identity_match_confirmed_targets(self):
        req = request("STOP_PROCESSES", targets=[dict(pid=12, create_time=1)])
        result = dict(ok=True, code="PROCESS_BATCH_RESULT", all_terminated=False,
                      counts={"PROTECTED": 1}, results=[dict(pid=12, create_time=1, ok=False, code="PROTECTED")])
        command_result(req, result)
        for changes in (dict(all_terminated=True), dict(counts={"TERMINATED": 1}),
                        dict(results=[dict(pid=13, create_time=1, ok=False, code="PROTECTED")])):
            with self.assertRaises(ValueError):
                command_result(req, result | changes)


class StorageRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "audit.sqlite3"
        self.store = AuditStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.state = MonitoringServer({"admin_token": "test", "agents": {"pc": "test"}}, audit=self.store)
        self.admin, self.agent = Mock(spec=ssl.SSLSocket), Mock(spec=ssl.SSLSocket)
        self.session = Session(self.admin)
        self.state.connections[self.admin] = self.session
        self.state.sessions["pc"] = self.agent
        self.state.send = Mock()

    def test_event_duplicate_is_not_stored_twice_even_after_reopen(self):
        sample = event()
        self.assertTrue(self.state.receive_exam_event("pc", sample))
        self.assertFalse(self.state.receive_exam_event("pc", sample))
        self.store.close()
        self.store = AuditStore(self.path)
        self.state.audit = self.store
        self.assertFalse(self.state.receive_exam_event("pc", sample))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM exam_events").fetchone()[0], 1)

    def test_rate_limit_rejects_flood(self):
        self.state.event_rates["pc"] = (time.monotonic(), 200)
        with self.assertRaises(ValueError):
            self.state.receive_exam_event("pc", event())

    def test_new_actions_route_and_audit_without_credentials(self):
        req = request(token="must-not-be-stored")
        self.state.route_command(self.admin, req)
        self.state.send.assert_called_once_with(self.agent, req)
        result = dict(type="COMMAND_RESULT", pc_id="pc", command_id=req["command_id"], ok=True,
                      code="EXAM_STATUS", exam_mode=dict(enabled=False, exam_id=None,
                      forbidden_processes=[], poll_interval=None, monitor_status="off"))
        self.state.route_result(self.agent, "pc", result)
        self.assertEqual(self.session.notifications.get_nowait(), result)
        row = self.store.db.execute("SELECT executor, request, result, outcome FROM command_audit").fetchone()
        self.assertEqual(row[0], self.session.executor_id)
        self.assertNotIn("must-not-be-stored", row[1])
        self.assertEqual(row[3], "result_received")

    def test_server_restart_blocks_command_replay(self):
        req = request()
        self.state.route_command(self.admin, req)
        self.store.close()
        self.store = AuditStore(self.path)
        self.state = MonitoringServer({"admin_token": "test"}, audit=self.store)
        self.state.sessions["pc"] = self.agent
        self.state.send = Mock()
        self.state.route_command(self.admin, req)
        self.assertEqual(self.state.send.call_args.args[1]["code"], "DUPLICATE_COMMAND")
        self.assertFalse(self.state.pending_commands)

    def test_invalid_command_is_never_forwarded_or_reserved(self):
        req = request("STOP_PROCESSES", targets=[])
        self.state.route_command(self.admin, req)
        self.state.send.assert_called_once()
        self.assertEqual(self.state.send.call_args.args[0], self.admin)
        self.assertEqual(self.state.send.call_args.args[1]["code"], "INVALID_TARGETS")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM command_audit").fetchone()[0], 0)


class RttSocketTests(unittest.TestCase):
    setUp = week3.Week3Tests.setUp
    tearDown = week3.Week3Tests.tearDown
    connect = week3.Week3Tests.connect
    barrier = week3.Week3Tests.barrier

    def test_ping_pong_and_rtt_metrics_preserved(self):
        conn, result = self.connect()
        self.assertTrue(result["rtt_v1"])
        self.assertNotIn("exam_events_v1", result)
        send_message(conn, dict(type="RTT_PING", pc_id="pc", probe_id="probe-1"))
        self.assertEqual(recv_message(conn), dict(type="RTT_PONG", pc_id="pc", probe_id="probe-1"))
        sample = metric(rtt_ms=24.2, rtt_status="ok", rtt_timestamp=datetime.now(timezone.utc).isoformat())
        send_message(conn, sample)
        self.barrier(conn)
        self.assertEqual(self.state.snapshot()["devices"][0]["metrics"]["rtt_ms"], 24.2)

    def test_spoofed_rtt_is_rejected(self):
        conn, _ = self.connect()
        send_message(conn, dict(type="RTT_PING", pc_id="other", probe_id="probe-1"))
        self.assertEqual(recv_message(conn)["type"], "ERROR")
