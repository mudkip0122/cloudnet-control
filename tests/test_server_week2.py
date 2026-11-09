"""Week 2 server acceptance: sessions, framing, heartbeat and status relay."""
import socket
import struct
import threading
import time
import unittest

from common.protocol import MAX_MESSAGE_BYTES, recv_message, send_message
from server.main import MonitoringServer, Session


class ServerWeek2Tests(unittest.TestCase):
    def setUp(self):
        self.state = MonitoringServer(heartbeat_timeout=0.8)
        self.clients = []
        self.threads = []

    def tearDown(self):
        for conn in self.clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def connect(self, message=None):
        client, server = socket.socketpair()
        client.settimeout(2)
        self.clients.append(client)
        def serve():
            with server:
                self.state.handle(server)
        thread = threading.Thread(target=serve, daemon=True)
        self.threads.append(thread)
        thread.start()
        if message:
            send_message(client, message)
            self.assertTrue(recv_message(client)["ok"])
        return client

    def agent(self, pc_id):
        return self.connect({"type": "REGISTER", "role": "agent",
                             "pc_id": pc_id, "pc_name": pc_id})

    def test_status_subscription_and_three_agent_failure_isolation(self):
        admin = self.connect({"type": "REGISTER", "role": "admin", "subscribe_status": True})
        agents = []
        for pc_id in ("one", "two", "three"):
            agents.append(self.agent(pc_id))
            update = recv_message(admin)
            self.assertEqual(update["type"], "STATUS_UPDATE")
            self.assertEqual(update["device"]["pc_id"], pc_id)
            self.assertTrue(update["device"]["online"])
        agents[1].shutdown(socket.SHUT_RDWR)
        update = recv_message(admin)
        self.assertEqual(update["device"]["pc_id"], "two")
        self.assertFalse(update["device"]["online"])
        send_message(admin, {"type": "LIST_DEVICES"})
        devices = {d["pc_id"]: d["online"] for d in recv_message(admin)["devices"]}
        self.assertEqual(devices, {"one": True, "two": False, "three": True})

    def test_heartbeat_keeps_agent_alive_then_expires_and_allows_reconnect(self):
        agent = self.agent("one")
        for _ in range(6):
            send_message(agent, {"type": "HEARTBEAT", "pc_id": "one"})
            time.sleep(0.2)
        self.assertTrue(self.state.snapshot()["devices"][0]["online"])
        self.threads[0].join(timeout=2)
        self.assertFalse(self.state.snapshot()["devices"][0]["online"])
        self.agent("one")
        self.assertTrue(self.state.snapshot()["devices"][0]["online"])

    def test_partial_frame_does_not_extend_liveness(self):
        agent = self.agent("one")
        agent.sendall(struct.pack("!I", 100))
        for _ in range(6):
            try:
                agent.sendall(b" ")
            except OSError:
                break
            time.sleep(0.2)
        self.threads[0].join(timeout=1)
        self.assertFalse(self.threads[0].is_alive())
        self.assertFalse(self.state.snapshot()["devices"][0]["online"])

    def test_heartbeat_spoof_and_admin_write_are_rejected(self):
        agent = self.agent("one")
        send_message(agent, {"type": "HEARTBEAT", "pc_id": "other"})
        self.assertEqual(recv_message(agent)["type"], "ERROR")
        admin = self.connect({"type": "REGISTER", "role": "admin"})
        send_message(admin, {"type": "METRICS", "pc_id": "one"})
        self.assertEqual(recv_message(admin)["type"], "ERROR")

    def test_bad_frames_and_unauthenticated_requests_leave_server_usable(self):
        for frame in (struct.pack("!I", 0), struct.pack("!I", MAX_MESSAGE_BYTES + 1),
                      struct.pack("!I", 1) + b"{", struct.pack("!I", 1) + b"\xff"):
            client = self.connect()
            client.sendall(frame)
            self.assertEqual(recv_message(client)["type"], "ERROR")
        client = self.connect()
        send_message(client, {"type": "LIST_DEVICES"})
        self.assertEqual(recv_message(client)["type"], "ERROR")
        self.agent("healthy")

    def test_concurrent_sends_produce_complete_frames(self):
        reader, writer = socket.socketpair()
        with reader, writer:
            reader.settimeout(3)
            session = Session(writer)
            def transmit(sender):
                for index in range(12):
                    session.send({"type": "TEST", "sender": sender, "index": index,
                                  "payload": "x" * 4000})
            threads = [threading.Thread(target=transmit, args=(i,)) for i in range(3)]
            for thread in threads:
                thread.start()
            received = [recv_message(reader) for _ in range(36)]
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            self.assertEqual({(m["sender"], m["index"]) for m in received},
                             {(s, i) for s in range(3) for i in range(12)})


if __name__ == "__main__":
    unittest.main()
