"""GUI -> TLS relay -> real Agent; terminate only our disposable test child."""
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import customtkinter as ctk

from admin_gui.main import Dashboard
from server.main import MonitoringServer
from tests.processes import stop_process


def main():
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        cert, key = directory / "test.crt", directory / "test.key"
        config = directory / "openssl.cnf"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n"
            "[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
            "extendedKeyUsage=serverAuth\n", encoding="ascii")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True, timeout=15)
        token = directory / "agent.txt"
        token.write_text("test-agent-token", encoding="ascii")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        state = MonitoringServer("test-admin-token", "test-agent-token")
        stopping = threading.Event()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(.2)
        port = listener.getsockname()[1]
        sessions, workers = [], []

        def serve(raw):
            try:
                raw.settimeout(5)
                with raw, tls.wrap_socket(raw, server_side=True) as conn:
                    sessions.append(conn)
                    state.handle(conn)
            except OSError:
                pass

        def accept():
            while not stopping.is_set():
                try:
                    raw, _ = listener.accept()
                except socket.timeout:
                    continue
                worker = threading.Thread(target=serve, args=(raw,), daemon=True)
                workers.append(worker)
                worker.start()

        acceptor = threading.Thread(target=accept, daemon=True)
        acceptor.start()
        agent = target = dashboard = None
        root = ctk.CTk()
        root.withdraw()

        def pump_until(predicate, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                if predicate():
                    return
                time.sleep(.02)
            process_window = dashboard.process_window if dashboard is not None else None
            detail = (process_window.status.get(), process_window.outcome.get(), process_window.pending) if process_window else None
            log = directory / "agent.log"
            raise AssertionError(f"GUI process TLS condition timed out: {detail}; agent log: "
                                 + (log.read_text(encoding="utf-8") if log.exists() else "no log"))

        try:
            target = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(120)"])
            agent = subprocess.Popen([sys.executable, "-m", "agent.main", "--host", "localhost", "--port", str(port),
                "--ca-file", str(cert), "--token-file", str(token), "--pc-id", "process-gui-test",
                "--live", "--enable-process-control", "--log-file", str(directory / "agent.log")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            dashboard = Dashboard(root, port, "localhost", ssl.create_default_context(cafile=cert), "test-admin-token")
            pump_until(lambda: dashboard.table.exists("process-gui-test"))
            dashboard.table.selection_set("process-gui-test")
            dashboard.open_processes()
            window = dashboard.process_window
            window.refresh()
            pump_until(lambda: window.complete)
            assert len(window.rows) > 40, "Expected multiple process result pages"
            assert str(target.pid) in window.rows
            assert window.rows[str(target.pid)]["can_stop"]
            protected = next(key for key, value in window.rows.items() if not value["can_stop"])
            window.table.selection_set(protected)
            window.update_buttons()
            assert str(window.stop_button["state"]) == "disabled"
            window.table.selection_set(str(target.pid))
            window.update_buttons()
            with patch("admin_gui.process_window.messagebox.askokcancel", return_value=True):
                window.stop_selected()
            pump_until(lambda: window.complete and str(target.pid) not in window.rows)
            target.wait(timeout=5)
            assert "종료를 확인" in window.outcome.get(), window.outcome.get()
            assert agent.poll() is None
            assert dashboard.connected
            dashboard.close()
            pump_until(lambda: not dashboard.worker.is_alive()
                       and not window.client.worker.is_alive())
            print("PASS: real GUI/TLS relay/Agent, complete multipage inventory, protected disabled, disposable PID stop and automatic refresh")
        finally:
            if dashboard is not None and not dashboard.closed:
                dashboard.close()
            for process in (agent, target):
                if process is not None:
                    stop_process(process)
            stopping.set()
            acceptor.join(1)
            listener.close()
            for conn in sessions:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            for worker in workers:
                worker.join(2)
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
