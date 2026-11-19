"""Local-only camera preview using synthetic JPEG frames."""
import base64
from datetime import datetime, timezone
from io import BytesIO
import queue
from types import SimpleNamespace

import customtkinter as ctk
from PIL import Image, ImageDraw
from tkinter import ttk

from admin_gui.exam_events import timestamp
from admin_gui.exam_video import LatestCameraFrame
from admin_gui.sample import build_sample


def main():
    root = ctk.CTk()
    dashboard = build_sample(root)
    root.after_cancel(dashboard.timer)
    dashboard.tick()
    frames = LatestCameraFrame()
    dashboard.exam_client = SimpleNamespace(
        camera_frames=frames, connection=queue.Queue(), events=queue.Queue(),
        close=lambda: frames.select(None), worker=SimpleNamespace(is_alive=lambda: False),
    )
    dashboard.exam_stream_connected = True
    dashboard.exam_generation = 1
    pc_id = "sample-pc-01"
    exam_id = "sample-exam"
    dashboard.exam_store.apply_chrome_event({
        "kind": "EXAM_CAMERA_STATUS", "pc_id": pc_id, "status": "on",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    dashboard.open_exam()
    exam = dashboard.exam_window
    exam.window.title("시험 모드 — 카메라 테스트")
    exam.window.geometry("1040x755")
    paused = False
    sequence = 0


    def toggle_frames():
        nonlocal paused
        paused = not paused
        toggle.configure(text="프레임 다시 보내기" if paused else "프레임 끊기 (3초 테스트)")


    def send_frame():
        nonlocal sequence
        if exam.closed or dashboard.closed:
            return
        if not paused:
            image = Image.new("RGB", (320, 208), "#102750")
            draw = ImageDraw.Draw(image)
            for index, color in enumerate(("#e3edfb", "#4fccf3", "#00a994", "#f9bd44", "#eb674c", "#9557d9")):
                draw.rectangle((16 + index * 48, 62, 60 + index * 48, 132), fill=color)
            draw.text((16, 18), "CLOUDNET  /  CAMERA TEST", fill="white")
            draw.text((16, 38), "SAMPLE-PC-01   SYNTHETIC JPEG", fill="#bed8fc")
            draw.line((16, 157, 300, 157), fill="#7395c7", width=2)
            x = 18 + sequence * 17 % 280
            draw.ellipse((x - 6, 151, x + 6, 163), fill="#f9bd44")
            draw.text((16, 177), f"FRAME {sequence:04d}", fill="white")
            payload = BytesIO()
            image.save(payload, format="JPEG", quality=82)
            frames.publish(1, dict(version=1, pc_id=pc_id, exam_id=exam_id, format="jpeg",
                                   timestamp=datetime.now(timezone.utc).isoformat(),
                                   data=base64.b64encode(payload.getvalue()).decode("ascii")), timestamp)
            sequence += 1
        root.after(500, send_frame)


    toggle = ttk.Button(exam.video_label.master.master, text="프레임 끊기 (3초 테스트)", command=toggle_frames)
    toggle.pack(fill="x", pady=(4, 5), before=exam.video_label.master)
    exam.window.lift()
    exam.window.focus_force()
    root.after(300, send_frame)
    root.mainloop()


if __name__ == '__main__':
    main()
