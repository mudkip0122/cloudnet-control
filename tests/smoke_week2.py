"""Three simulated PC sessions, TCP admin, real GUI and delayed server reply."""
import socket
import threading
import time

import customtkinter as ctk

from admin_gui.main import Dashboard
from common.protocol import recv_message, send_message
from server.main import MonitoringServer


def main():
    state = MonitoringServer()
    stopping = threading.Event()
    delay_reply = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    sockets, workers = [], []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(.2)

    def serve(conn):
        with conn:
            try:
                conn.settimeout(5)
                registration = recv_message(conn)
                if registration.get("role") == "admin":
                    send_message(conn, {"type": "REGISTER_RESULT", "ok": True})
                    while not stopping.is_set():
                        assert recv_message(conn)["type"] == "LIST_DEVICES"
                        if delay_reply.is_set():
                            waiting.set()
                            release.wait(4)
                        send_message(conn, state.snapshot())
            except (OSError, EOFError):
                pass

    # Agents use socket pairs and the production handler; admin uses TCP.
    def accept_admin():
        while not stopping.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            sockets.append(conn)
            worker = threading.Thread(target=serve, args=(conn,), daemon=True)
            workers.append(worker)
            worker.start()

    acceptor = threading.Thread(target=accept_admin, daemon=True)
    acceptor.start()
    root = ctk.CTk()
    root.withdraw()
    dashboard = Dashboard(root, listener.getsockname()[1])

    def pump_until(predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("GUI condition timed out")

    try:
        agents = []
        for index in range(3):
            client, peer = socket.socketpair()
            sockets.extend((client, peer))
            def agent_handler(peer=peer):
                with peer:
                    state.handle(peer)
            worker = threading.Thread(target=agent_handler, daemon=True)
            workers.append(worker)
            worker.start()
            send_message(client, {"type": "REGISTER", "role": "agent",
                                  "pc_id": f"pc-{index}", "pc_name": f"SIMULATED PC {index}"})
            assert recv_message(client)["ok"]
            agents.append(client)
        pump_until(lambda: len(dashboard.table.get_children()) == 3)
        assert all(dashboard.devices[f"pc-{i}"]["online"] for i in range(3))
        for index, agent in enumerate(agents):
            agent.shutdown(socket.SHUT_RDWR)
            pump_until(lambda index=index: not dashboard.devices[f"pc-{index}"]["online"])
            assert "오프라인" in dashboard.table.item(f"pc-{index}", "values")

        delay_reply.set()
        pump_until(waiting.is_set)
        heartbeats = []
        def heartbeat():
            heartbeats.append(time.monotonic())
            if len(heartbeats) < 10:
                root.after(40, heartbeat)
        root.after(40, heartbeat)
        pump_until(lambda: len(heartbeats) == 10, timeout=2)
        assert not release.is_set() and dashboard.worker.is_alive()
        release.set()
        dashboard.close()
        pump_until(lambda: not dashboard.worker.is_alive())
        print("PASS: 3 simulated PC connections/disconnections in GUI; 10 GUI callbacks during blocked server reply")
    finally:
        stopping.set()
        release.set()
        if not dashboard.closed:
            dashboard.close()
        for conn in sockets:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        acceptor.join(1)
        listener.close()
        for worker in workers:
            worker.join(1)


if __name__ == "__main__":
    main()
