import socket
import struct
import threading
import unittest

from common.protocol import MAX_MESSAGE_BYTES, recv_message, send_message
from server.main import handle_client


class ProtocolTests(unittest.TestCase):
    def test_fragmented_and_consecutive_messages(self):
        reader, writer = socket.socketpair()
        with reader, writer:
            reader.settimeout(2)
            def transmit():
                with writer:
                    payload = b'{"type":"ONE"}'
                    packet = struct.pack("!I", len(payload)) + payload
                    for byte in packet:
                        writer.sendall(bytes([byte]))
                    send_message(writer, {"type": "TWO"})
            thread = threading.Thread(target=transmit)
            thread.start()
            self.assertEqual(recv_message(reader), {"type": "ONE"})
            self.assertEqual(recv_message(reader), {"type": "TWO"})
            thread.join(timeout=2)

    def test_oversized_frame_rejected_before_body(self):
        reader, writer = socket.socketpair()
        with reader, writer:
            writer.sendall(struct.pack("!I", MAX_MESSAGE_BYTES + 1))
            with self.assertRaises(ValueError):
                recv_message(reader)

    def test_truncated_frame(self):
        reader, writer = socket.socketpair()
        with reader, writer:
            writer.sendall(struct.pack("!I", 10) + b"{")
            writer.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                recv_message(reader)

    def test_register_success_and_invalid_registration(self):
        for message, expected in [
            ({"type": "REGISTER", "role": "agent", "pc_id": "pc-b", "pc_name": "B"}, "REGISTER_RESULT"),
            ({"type": "REGISTER", "role": "agent", "pc_id": "", "pc_name": "B"}, "ERROR"),
            ({"type": "METRICS"}, "ERROR"),
        ]:
            with self.subTest(message=message):
                client, server = socket.socketpair()
                with client, server:
                    client.settimeout(2)
                    thread = threading.Thread(target=handle_client, args=(server,))
                    thread.start()
                    send_message(client, message)
                    response = recv_message(client)
                    client.shutdown(socket.SHUT_RDWR)
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(response["type"], expected)
                    if expected == "REGISTER_RESULT":
                        self.assertTrue(response["ok"])
                        self.assertEqual(response["pc_id"], "pc-b")


if __name__ == "__main__":
    unittest.main()
