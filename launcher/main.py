"""Double-click owner launcher. Closing its dashboard stops the server and EC2."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from launcher.config import CONFIG_FILE, ROOT, load_config, save_config, validate
from launcher.lifecycle import AwsInstance, Cancelled, CREATE_NO_WINDOW, Session, SshService, client_command


class SingleInstance:
    def __enter__(self):
        path = ROOT / "runtime" / "launcher.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("CloudNet 실행기가 이미 열려 있습니다. 기존 창을 사용하세요.")
        return self

    def __exit__(self, *args):
        self.file.close()


class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.children = []
        self.pending_session = None
        root.title("CloudNet Control — EC2 실행기")
        root.geometry("650x560")
        root.minsize(600, 520)
        body = ttk.Frame(root, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="CloudNet Control", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(body, text="시작: EC2 → 서버 → GUI   /   GUI 닫기: 서버 종료 → EC2 중지").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 16))
        self.fields = {}
        for row, (key, label) in enumerate((("instance_id", "EC2 인스턴스 ID"), ("region", "AWS 리전"),
                                           ("profile", "AWS 프로필"), ("ssh_key", "SSH 키 파일")), 2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            variable = tk.StringVar(value=self.config[key])
            self.fields[key] = variable
            ttk.Entry(body, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew")
        ttk.Button(body, text="키 파일 선택", command=self.choose_key).grid(row=6, column=2, sticky="e")
        self.with_agent = tk.BooleanVar(value=self.config["start_local_agent"])
        ttk.Checkbutton(body, text="본인 PC의 Agent도 함께 실행", variable=self.with_agent).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(body, text="AWS API 인증은 이 PC의 AWS 프로필을 사용합니다.\n"
                  "GUI를 닫으면 팀원들의 연결도 끊깁니다.").grid(
                      row=7, column=0, columnspan=3, sticky="w", pady=8)
        self.start_button = ttk.Button(body, text="EC2 · 서버 · GUI 시작", command=self.start)
        self.start_button.grid(row=8, column=0, columnspan=2, sticky="ew", pady=10)
        self.stop_button = ttk.Button(body, text="서버 · EC2 종료", command=self.close, state="disabled")
        self.stop_button.grid(row=8, column=2, sticky="ew", padx=(10, 0))
        self.status = tk.StringVar(value="최초 한 번 AWS 프로필과 인스턴스 ID를 설정하세요.")
        ttk.Label(body, textvariable=self.status, wraplength=600).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=8)
        self.log = tk.Text(body, height=9, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew")
        self.recover_button = ttk.Button(body, text="이전 실행의 EC2 중지 / 중지 재시도", command=self.recover)
        self.recover_button.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        body.rowconfigure(10, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        record_file = ROOT / "runtime" / "launcher-session.json"
        stale = record_file.exists() and json.loads(record_file.read_text(encoding="utf-8")).get("needs_stop")
        if stale:
            self.status.set("이전 실행의 EC2 중지가 확인되지 않았습니다. 중지/재시도 버튼으로 먼저 정리하세요.")
        elif CONFIG_FILE.exists() and self.config["instance_id"]:
            root.after(300, self.start)

    def choose_key(self):
        path = filedialog.askopenfilename(title="본인 SSH 개인키 선택", filetypes=[("PEM key", "*.pem"), ("All", "*")])
        if path:
            self.fields["ssh_key"].set(path)

    def report(self, text):
        self.messages.put(("status", text))

    def start(self):
        if self.running:
            return
        if self.pending_session is not None and self.pending_session.must_stop:
            self.recover()
            return
        config = dict(self.config)
        config.update({key: value.get().strip() for key, value in self.fields.items()})
        config["start_local_agent"] = self.with_agent.get()
        try:
            validate(config)
            save_config(config)
        except Exception as exc:
            messagebox.showerror("설정 확인", str(exc), parent=self.root)
            return
        self.config = config
        self.cancel.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.recover_button.configure(state="disabled")
        threading.Thread(target=self.work, args=(dict(config),), daemon=False).start()

    def recover(self):
        if self.running:
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.recover_button.configure(state="disabled")
        threading.Thread(target=self.recover_work, daemon=False).start()

    def recover_work(self):
        errors = []
        session = self.pending_session
        try:
            if session is None or not session.must_stop:
                path = ROOT / "runtime" / "launcher-session.json"
                if not path.exists():
                    raise ValueError("중지할 이전 실행 기록이 없습니다.")
                record = json.loads(path.read_text(encoding="utf-8"))
                if not record.get("needs_stop"):
                    raise ValueError("이전 EC2 중지는 이미 확인됐습니다.")
                config = dict(self.config)
                config.update({key: record[key] for key in ("instance_id", "region", "profile")})
                aws = AwsInstance(config)
                session = Session(config, aws, SshService(config), self.report)
                session.must_stop = True
                instance = aws.describe()
                session.host = instance.get("PublicIpAddress")
                session.service_attempted = bool(session.host)
            errors.extend(session.stop())
            self.pending_session = session if session.must_stop else None
        except Exception as exc:
            errors.append(str(exc))
        self.messages.put(("done", errors))

    def spawn(self, args, log):
        process = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW)
        self.children.append(process)
        return process

    def work(self, config):
        session = None
        errors = []
        try:
            # Validate TLS material and local dependencies before any EC2 mutation.
            import ssl
            from launcher.config import local_path
            ssl.create_default_context(cafile=str(local_path(config["admin_ca_file"])))
            if config["start_local_agent"]:
                import psutil  # noqa: F401
            service = SshService(config)
            aws = AwsInstance(config)
            aws.verify_permissions()
            session = Session(config, aws, service, self.report, self.cancel)
            host = session.start()
            endpoint = {"host": host, "port": config["server_port"], "server_name": config["certificate_name"]}
            (ROOT / "runtime" / "current-endpoint.json").write_text(json.dumps(endpoint, indent=2), encoding="utf-8")
            self.report(f"접속 주소: {host}:{config['server_port']}")
            if host != config["certificate_name"]:
                self.report("EC2 IP가 변경됐습니다. 팀원들은 runtime/current-endpoint.json의 새 주소로 접속하세요.")
            session.check_cancel()
            with (ROOT / "runtime" / "launcher-clients.log").open("a", encoding="utf-8") as log:
                if config["start_local_agent"]:
                    self.spawn(client_command(config, host, "agent.main", config["agent_token_file"], config["pc_id"]), log)
                gui = self.spawn(client_command(config, host, "admin_gui.main", config["admin_token_file"]), log)
                self.report("GUI 실행 중 · GUI를 닫으면 EC2가 중지됩니다.")
                while gui.poll() is None and not self.cancel.wait(0.2):
                    pass
                if gui.poll() not in (None, 0):
                    errors.append("GUI 실행 오류입니다. runtime/launcher-clients.log를 확인하세요.")
        except Cancelled:
            self.report("실행을 취소했습니다. 정리 중…")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.messages.put(("stopping", None))
            for process in reversed(self.children):
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                except Exception as exc:
                    errors.append(str(exc))
            self.children.clear()
            if session:
                errors.extend(session.stop())
                self.pending_session = session if session.must_stop else None
            self.messages.put(("done", errors))

    def close(self):
        if not self.running:
            if self.pending_session is not None and self.pending_session.must_stop:
                self.recover()
                return
            self.root.destroy()
            return
        self.cancel.set()
        self.stop_button.configure(state="disabled")
        self.status.set("종료 요청을 받았습니다. EC2 중지 확인까지 창을 유지하세요.")

    def poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == "status":
                    self.status.set(data)
                    self.log.configure(state="normal")
                    self.log.insert("end", data + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "stopping":
                    self.root.deiconify()
                    self.stop_button.configure(state="disabled")
                    self.recover_button.configure(state="disabled")
                elif kind == "done":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.recover_button.configure(state="normal")
                    if data:
                        self.status.set("완료하지 못한 작업이 있습니다. 아래 오류와 AWS 콘솔을 확인하세요.")
                        messagebox.showerror("실행/종료 확인 필요", "\n\n".join(data), parent=self.root)
                    else:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    with SingleInstance():
        root = tk.Tk()
        App(root)
        root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "CloudNet 실행 오류", 0x10)
        else:
            raise
