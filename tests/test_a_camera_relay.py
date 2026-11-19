"""TLS camera relay: scoped viewers, consent state and bounded in-memory frames."""
import base64
from datetime import datetime, timezone
from io import BytesIO
import socket
from unittest.mock import patch

from PIL import Image
from common.protocol import send_message, recv_message
from tests.test_server_exam_stream import ExamStreamTests


class CameraRelayTests(ExamStreamTests):
    def test_camera_subscription_rejects_wrong_role_and_unknown_pc(self):
        _, result = self.connect('agent', subscribe_camera={'pc_id': 'PC-01', 'exam_id': 'exam-1'})
        self.assertEqual(result['type'], 'ERROR')
        _, result = self.connect(subscribe_camera={'pc_id': 'UNKNOWN', 'exam_id': 'exam-1'})
        self.assertEqual(result['type'], 'ERROR')
        unauthenticated = self.open_tls()
        send_message(unauthenticated, {'type': 'REGISTER', 'role': 'admin', 'token': 'wrong',
                                      'subscribe_camera': {'pc_id': 'PC-01', 'exam_id': 'exam-1'}})
        self.assertEqual(recv_message(unauthenticated)['type'], 'ERROR')

    def test_authorized_active_exam_only_and_no_persistence(self):
        agent, registered = self.connect('agent')
        self.assertTrue(registered.get('exam_camera_stream_v1'))
        viewer, _ = self.connect(subscribe_camera={'pc_id': 'PC-01', 'exam_id': 'exam-1'})
        other, _ = self.connect(subscribe_camera={'pc_id': 'PC-02', 'exam_id': 'exam-1'})
        admin, _ = self.connect()
        for conn in (viewer, other):
            send_message(conn, {'type': 'LIST_DEVICES'})
            self.assertEqual(recv_message(conn)['type'], 'DEVICE_LIST')
        self.enable(agent, admin)
        status = dict(type='EXAM_EVENT', version=1, pc_id='PC-01', exam_id='exam-1',
                      event_id='camera-on', kind='EXAM_CAMERA_STATUS', status='on',
                      timestamp=datetime.now(timezone.utc).isoformat())
        send_message(agent, status)
        self.barrier(agent)
        output = BytesIO()
        Image.new('RGB', (4, 4), 'black').save(output, format='JPEG')
        frame = dict(type='EXAM_CAMERA_FRAME', version=1, pc_id='PC-01', exam_id='exam-1',
                     timestamp=datetime.now(timezone.utc).isoformat(), format='jpeg',
                     data=base64.b64encode(output.getvalue()).decode())
        send_message(agent, frame)
        self.assertEqual(recv_message(viewer), frame)
        other.settimeout(.2)
        with self.assertRaises(socket.timeout):
            recv_message(other)
        status.update(event_id='camera-off', status='off')
        send_message(agent, status)
        send_message(agent, frame)
        self.barrier(agent)
        viewer.settimeout(.2)
        with self.assertRaises(socket.timeout):
            recv_message(viewer)
        rows = self.audit.db.execute('SELECT payload FROM exam_events').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('EXAM_CAMERA_FRAME' not in payload for (payload,) in rows))

    def test_malformed_and_oversize_frames_are_rejected(self):
        from server.camera import camera_frame
        frame = dict(type='EXAM_CAMERA_FRAME', version=1, pc_id='PC-01', exam_id='exam-1',
                     timestamp=datetime.now(timezone.utc).isoformat(), format='jpeg', data='!!!!')
        with self.assertRaises(ValueError):
            camera_frame(frame, 'PC-01')
        with self.assertRaises(ValueError):
            camera_frame(frame | {'data': 'A' * 40000}, 'PC-01')
