"""Test B power simulation through the authenticated A relay; no real power action."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import patch
import time

from agent.power_commands import PowerCommands
from common.protocol import recv_message, send_message
from tests.test_server_exam_stream import ExamStreamTests


class PowerRelayTests(ExamStreamTests):
    def test_timeout_is_recorded_and_notifies_admin(self):
        agent, _ = self.connect('agent', power_control_mode='simulate', boot_id=None)
        admin, _ = self.connect()
        request = {'type': 'COMMAND', 'command_id': 'power-timeout', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'simulate', 'target_boot_id': None,
                   'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=.6)).isoformat()}
        send_message(admin, request)
        self.assertEqual(recv_message(agent), request)
        self.assertEqual(recv_message(admin)['code'], 'COMMAND_TIMEOUT')
        row = self.audit.db.execute("SELECT outcome FROM command_audit WHERE command_id='power-timeout'").fetchone()
        self.assertEqual(row[0], 'timeout_unconfirmed')
        stages = self.audit.db.execute("SELECT stage FROM command_transitions WHERE command_id='power-timeout' ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in stages], ['request_accepted', 'timeout_unconfirmed'])

    def test_timeout_after_acceptance_keeps_acceptance_record(self):
        agent, _ = self.connect('agent', power_control_mode='simulate', boot_id=None)
        admin, _ = self.connect()
        request = {'type': 'COMMAND', 'command_id': 'accepted-timeout', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'simulate', 'target_boot_id': None,
                   'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
        send_message(admin, request)
        recv_message(agent)
        send_message(agent, {'type': 'COMMAND_RESULT', 'pc_id': 'PC-01', 'command_id': 'accepted-timeout',
                             'ok': True, 'code': 'POWER_ACCEPTED', 'terminal': False})
        self.assertEqual(recv_message(admin)['code'], 'POWER_ACCEPTED')
        with self.state.lock:
            self.state.pending_commands['accepted-timeout']['until'] = time.monotonic() - 1
        self.assertEqual(recv_message(admin)['code'], 'COMMAND_TIMEOUT')
        rows = self.audit.db.execute("SELECT stage, code FROM command_transitions WHERE command_id='accepted-timeout' ORDER BY id").fetchall()
        self.assertEqual(rows, [('request_accepted', None), ('partial_result', 'POWER_ACCEPTED'),
                                ('timeout_unconfirmed', None)])

    def test_unexpected_shell_fields_are_rejected(self):
        agent, _ = self.connect('agent', power_control_mode='simulate', boot_id=None)
        admin, _ = self.connect()
        request = {'type': 'COMMAND', 'command_id': 'shell', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'simulate', 'target_boot_id': None,
                   'shell': 'unexpected',
                   'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
        send_message(admin, request)
        self.assertEqual(recv_message(admin)['code'], 'INVALID_COMMAND_FIELDS')
        self.barrier(agent)

    def test_reboot_confirmation_requires_new_boot_after_execution(self):
        now = time.time()
        old_boot = datetime.fromtimestamp(now - 600, timezone.utc).isoformat()
        new_boot = datetime.fromtimestamp(now + 1, timezone.utc).isoformat()
        request = {'type': 'COMMAND', 'command_id': 'reboot', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'execute',
                   'target_boot_id': old_boot}
        self.assertTrue(self.audit.reserve(request, 'admin', datetime.now(timezone.utc).isoformat()))
        self.audit.result('reboot', {'type': 'COMMAND_RESULT', 'pc_id': 'PC-01', 'command_id': 'reboot',
                                    'ok': True, 'code': 'POWER_EXECUTION_STARTED', 'terminal': True})
        self.audit.register_device('PC-01', 'test', now + 1, old_boot)
        row = self.audit.db.execute("SELECT reboot_confirmed_at FROM command_audit WHERE command_id='reboot'").fetchone()
        self.assertIsNone(row[0])
        self.audit.register_device('PC-01', 'test', now + 1, datetime.fromtimestamp(now - 1000, timezone.utc).isoformat())
        row = self.audit.db.execute("SELECT reboot_confirmed_at FROM command_audit WHERE command_id='reboot'").fetchone()
        self.assertIsNone(row[0])
        self.audit.register_device('PC-01', 'test', now + 2, new_boot)
        row = self.audit.db.execute("SELECT reboot_confirmed_at, observed_boot_id FROM command_audit WHERE command_id='reboot'").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], new_boot)

    def test_power_simulation_and_durable_duplicate_protection(self):
        agent, registration = self.connect('agent', power_control_mode='simulate', boot_id=None)
        self.assertTrue(registration.get('power_control_v1'))
        admin, _ = self.connect()
        send_message(admin, {'type': 'LIST_DEVICES'})
        device = recv_message(admin)['devices'][0]
        self.assertEqual((device['power_control_mode'], device['boot_id']), ('simulate', None))
        request = {'type': 'COMMAND', 'command_id': 'power-1', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'simulate', 'target_boot_id': None,
                   'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
        with tempfile.TemporaryDirectory() as folder, patch('agent.power_commands.request_windows_power') as power:
            handler = PowerCommands('PC-01', 'simulate', None, Path(folder) / 'power.db')
            send_message(admin, request)
            forwarded = recv_message(agent)
            self.assertEqual(forwarded, request)
            handler.handle(forwarded, lambda result: send_message(agent, result), server_supported=True)
            first, last = recv_message(admin), recv_message(admin)
            self.assertEqual((first['code'], first['terminal']), ('POWER_ACCEPTED', False))
            self.assertEqual((last['code'], last['terminal']), ('POWER_SIMULATED', True))
            power.assert_not_called()
            send_message(admin, request)
            self.assertEqual(recv_message(admin)['code'], 'DUPLICATE_COMMAND')
            self.assertEqual(self.audit.db.execute('SELECT outcome FROM command_audit WHERE command_id=?',
                                                  ('power-1',)).fetchone()[0], 'result_received')
            stages = self.audit.db.execute('SELECT stage, code FROM command_transitions WHERE command_id=? ORDER BY id', ('power-1',)).fetchall()
            self.assertEqual(stages, [('request_accepted', None), ('partial_result', 'POWER_ACCEPTED'),
                                      ('result', 'POWER_SIMULATED')])

    def test_mismatched_mode_and_boot_are_not_relayed(self):
        agent, _ = self.connect('agent', power_control_mode='simulate', boot_id=None)
        admin, _ = self.connect()
        request = {'type': 'COMMAND', 'command_id': 'power-bad', 'target_id': 'PC-01',
                   'action': 'SHUTDOWN_PC', 'power_control_mode': 'execute', 'target_boot_id': None,
                   'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
        send_message(admin, request)
        self.assertEqual(recv_message(admin)['code'], 'POWER_MODE_MISMATCH')
        request.update(command_id='power-bad-boot', power_control_mode='simulate', target_boot_id='old')
        send_message(admin, request)
        self.assertEqual(recv_message(admin)['code'], 'BOOT_ID_MISMATCH')
        self.barrier(agent)
