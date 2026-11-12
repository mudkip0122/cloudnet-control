"""A week-three live check: real PC-01 -> EC2 -> unchanged C widgets.

Starts/stops only its own Agent and an eight-second single-core test load.
Never starts exam mode, terminates user apps, or prints credentials/process rows.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import ssl
import subprocess
import sys
import time

import customtkinter as ctk

from admin_gui.main import Dashboard
from admin_gui.process_control import ProcessClient, command_request

ROOT = Path(__file__).resolve().parents[1]
HOST, SERVER_NAME = "13.238.97.144", "32.236.13.91"


def main():
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "real_pc": "PC-01", "synthetic": False}
    agent = load = dashboard = root = client = None
    log = (runtime / "week3-pc01-agent.log").open("w", encoding="utf-8")
    try:
        context = ssl.create_default_context(cafile=str(ROOT / "secrets/clients/admin/server.crt"))
        token = (ROOT / "secrets/clients/admin/secrets/token.txt").read_text().strip()
        agent_args = [sys.executable, "-m", "agent.main", "--host", HOST, "--port", "5000",
                      "--pc-id", "PC-01", "--ca-file", "secrets/agent/server.crt",
                      "--token-file", "secrets/agent/token.txt", "--server-name", SERVER_NAME,
                      "--live", "--enable-process-control"]
        agent = subprocess.Popen(agent_args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        root = ctk.CTk()
        root.withdraw()
        dashboard = Dashboard(root, 5000, HOST, context, token, SERVER_NAME)

        def wait_for(condition, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                if condition():
                    return
                time.sleep(.05)
            raise AssertionError("Timed out waiting for deployed server/GUI state")

        wait_for(lambda: dashboard.connected and "PC-01" in dashboard.devices
                 and dashboard.devices["PC-01"].get("metrics") is not None
                 and not dashboard.devices["PC-01"].get("metrics_stale"))
        dashboard.table.selection_set("PC-01")
        dashboard.show_detail()
        self_metrics = dashboard.devices["PC-01"]["metrics"]
        assert dashboard.usage_values["cpu"].get() == f'{self_metrics["cpu_percent"]:.1f} %'
        report["tls_gui_real_values"] = True
        report["devices_online"] = [pc for pc, d in dashboard.devices.items() if d.get("online")]
        report["gui_by_pc"] = {}
        peer_times = {}
        for pc_id in report["devices_online"]:
            device = dashboard.devices[pc_id]
            if not device.get("metrics") or device.get("metrics_stale"):
                continue
            dashboard.table.selection_set(pc_id)
            dashboard.show_detail()
            assert dashboard.usage_values["cpu"].get() == f'{device["metrics"]["cpu_percent"]:.1f} %'
            assert dashboard.charts.pc_id == pc_id
            report["gui_by_pc"][pc_id] = {"cpu_percent": device["metrics"]["cpu_percent"],
                                         "own_graph": True}
            peer_times[pc_id] = device["last_metrics_received"]
        dashboard.table.selection_set("PC-01")
        dashboard.show_detail()
        first = self_metrics["timestamp"]
        wait_for(lambda: dashboard.devices["PC-01"]["metrics"]["timestamp"] != first)
        baseline = dashboard.devices["PC-01"]["metrics"]["cpu_percent"]
        # Single bounded process, no system configuration changes or remote commands.
        code = "import time\nend=time.monotonic()+8\nwhile time.monotonic()<end:\n sum(i*i for i in range(10000))\n"
        load = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        observed = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            root.update()
            metrics = dashboard.devices["PC-01"]["metrics"]
            if not observed or metrics["timestamp"] != observed[-1][0]:
                observed.append((metrics["timestamp"], metrics["cpu_percent"]))
            time.sleep(.05)
        load.wait(timeout=2)
        load = None
        dashboard.show_detail()
        dashboard.charts.draw()
        assert len(observed) >= 3
        assert dashboard.charts.pc_id == "PC-01"
        assert len(dashboard.charts.points) >= 3
        report["cpu_before_load"] = baseline
        report["cpu_during_check"] = [value for _, value in observed]
        report["graph_real_samples"] = len(dashboard.charts.points)
        report["cpu_change_seen"] = max(value for _, value in observed) != min(value for _, value in observed)
        report["other_pc_updates_during_load"] = {
            pc_id: dashboard.devices[pc_id].get("online") and
            dashboard.devices[pc_id].get("last_metrics_received") != stamp
            for pc_id, stamp in peer_times.items() if pc_id != "PC-01"}
        assert all(report["other_pc_updates_during_load"].values())

        client = ProcessClient(HOST, 5000, context, token, server_name=SERVER_NAME)
        request = command_request("PC-01", "LIST_PROCESSES")
        client.submit(request)
        result_holder = []
        def received_result():
            try:
                result_holder.append(client.results.get_nowait())
            except queue.Empty:
                return False
            return True
        wait_for(received_result)
        returned_request, result = result_holder[0]
        assert returned_request["command_id"] == request["command_id"]
        assert result.get("ok") is True and result.get("code") == "PROCESS_LIST"
        report["c_process_pages_assembled"] = True
        report["process_count"] = len(result["processes"])
        report["peer_process_counts"] = {}
        for pc_id in report["devices_online"]:
            if pc_id == "PC-01":
                continue
            peer_request = command_request(pc_id, "LIST_PROCESSES")
            client.submit(peer_request)
            result_holder.clear()
            wait_for(received_result)
            returned_request, result = result_holder[0]
            assert returned_request["command_id"] == peer_request["command_id"]
            assert result.get("ok") is True and result.get("code") == "PROCESS_LIST"
            report["peer_process_counts"][pc_id] = (len(result["processes"]) if result.get("ok")
                                                    else result.get("code"))

        agent.terminate()
        agent.wait(timeout=5)
        agent = None
        wait_for(lambda: not dashboard.devices["PC-01"]["online"])
        dashboard.show_detail()
        assert dashboard.usage_values["cpu"].get() == "—"
        report["offline_hides_old_values"] = True
        report["peers_stay_online_after_disconnect"] = {
            pc_id: bool(dashboard.devices.get(pc_id, {}).get("online"))
            for pc_id in report["devices_online"] if pc_id != "PC-01"}
        assert all(report["peers_stay_online_after_disconnect"].values())
        agent = subprocess.Popen(agent_args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        wait_for(lambda: dashboard.devices["PC-01"]["online"]
                 and dashboard.devices["PC-01"].get("metrics") is not None
                 and not dashboard.devices["PC-01"].get("metrics_stale"))
        report["same_id_reconnect"] = True
        report["passed"] = True
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if load is not None and load.poll() is None:
            load.terminate()
            load.wait(timeout=5)
        if client is not None:
            client.close()
            client.worker.join(timeout=3)
        if dashboard is not None:
            # Remove pending visual refresh callbacks before asynchronously
            # closing workers; only this disposable test root is affected.
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback)
            dashboard.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if not root.winfo_exists():
                        break
                    root.update()
                except Exception:
                    break
                time.sleep(.05)
        if agent is not None and agent.poll() is None:
            agent.terminate()
            agent.wait(timeout=5)
        log.close()
        (runtime / "week3-pc01-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
