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
    def test_reboot_confirmation_requires_new_boot_after_execution(self):
        now = time.time()
        request = {'type': 'COMMAND', 'command_id': 'reboot', 'target_id': 'PC-01',
                   'action': 'REBOOT_PC', 'power_control_mode': 'execute',
                   'target_boot_id': '2026-11-19T00:00:00Z'}
        self.assertTrue(self.audit.reserve(request, 'admin', datetime.now(timezone.utc).isoformat()))
        self.audit.result('reboot', {'type': 'COMMAND_RESULT', 'pc_id': 'PC-01', 'command_id': 'reboot',
                                    'ok': True, 'code': 'POWER_EXECUTION_STARTED', 'terminal': True})
        self.audit.register_device('PC-01', 'test', now + 1, '2026-11-19T00:00:00Z')
        row = self.audit.db.execute("SELECT reboot_confirmed_at FROM command_audit WHERE command_id='reboot'").fetchone()
        self.assertIsNone(row[0])
        self.audit.register_device('PC-01', 'test', now + 2, '2026-11-19T00:01:00Z')
        row = self.audit.db.execute("SELECT reboot_confirmed_at, observed_boot_id FROM command_audit WHERE command_id='reboot'").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], '2026-11-19T00:01:00Z')

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
