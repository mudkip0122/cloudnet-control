"""Bounded per-PC measurement history and lightweight Tk charts."""
from collections import deque
from datetime import datetime
import tkinter as tk
from tkinter import ttk


class MetricHistory:
    def __init__(self, limit=180):
        self.limit = limit
        self.samples = {}

    def update(self, devices):
        self.samples = {key: value for key, value in self.samples.items() if key in devices}
        for pc_id, device in devices.items():
            metrics = device.get("metrics") or {}
            if not device.get("online") or not metrics.get("timestamp"):
                continue
            try:
                stamp = datetime.fromisoformat(metrics["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    continue
                seconds = stamp.timestamp()
            except (ValueError, TypeError):
                continue
            points = self.samples.setdefault(pc_id, deque(maxlen=self.limit))
            if points and seconds <= points[-1][0]:
                continue
            points.append((seconds, dict(metrics)))


class LiveCharts(ttk.LabelFrame):
    def __init__(self, parent):
        super().__init__(parent, text="선택 PC 네트워크 · 최근 180개 측정값 · 결측/6초 초과 간격은 끊어서 표시")
        self.canvas = tk.Canvas(self, height=210, background="#ffffff", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.points = ()
        self.state = "PC를 선택하세요"
        self.canvas.bind("<Configure>", lambda event: self.draw())

    def show(self, points, state):
        # Refresh the selected PC's source even when both PCs have identical traffic.
        self.points = tuple(points)
        signature = (tuple((p[0], tuple(p[1].get(k) for k in (
            "upload_mbps", "download_mbps"))) for p in self.points), state)
        if signature == getattr(self, "signature", None):
            return
        self.signature = signature
        self.state = state
        self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100:
            return
        panels = (("네트워크 (Mbps)", (("upload_mbps", "업로드", "#059669"), ("download_mbps", "다운로드", "#d97706")), None),)
        for panel, (title, fields, ceiling) in enumerate(panels):
            left, right = 58, width - 22
            top, bottom = 40, height - 34
            canvas.create_text(left, 14, text=title, anchor="w", fill="#334155")
            for index, (_, label, color) in enumerate(fields):
                canvas.create_text(right - index * 92, 14, text=label, anchor="e", fill=color)
            values = [m.get(field) for _, m in self.points for field, _, _ in fields if m.get(field) is not None]
            maximum = ceiling or max(1, max(values, default=0) * 1.15)
            for fraction in (0, .5, 1):
                y = bottom - fraction * (bottom - top)
                canvas.create_line(left, y, right, y, fill="#e2e8f0")
                canvas.create_text(left - 6, y, text=f"{fraction * maximum:.1f}", anchor="e", fill="#64748b")
            if not self.points:
                canvas.create_text((left + right) / 2, (top + bottom) / 2, text="측정 데이터 대기", fill="#64748b")
                continue
            start, end = self.points[0][0], self.points[-1][0]
            span = max(2, end - start)
            for stamp, anchor, x in ((start, "w", left), (end, "e", right)):
                canvas.create_text(x, bottom + 16, text=datetime.fromtimestamp(stamp).strftime("%H:%M:%S"), anchor=anchor, fill="#64748b")
            for field, _, color in fields:
                segment = []
                previous_stamp = None

                def draw_segment():
                    if len(segment) >= 4:
                        canvas.create_line(*segment, fill=color, width=2,
                                           smooth=True, splinesteps=24,
                                           capstyle="round", joinstyle="round")
                    elif segment:
                        x, y = segment
                        canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)

                for stamp, metrics in self.points:
                    value = metrics.get(field)
                    if value is None or (previous_stamp is not None and stamp - previous_stamp > 6):
                        draw_segment()
                        segment = []
                    if value is None:
                        previous_stamp = None
                        continue
                    x = left + (stamp - start) / span * (right - left)
                    y = bottom - min(maximum, max(0, value)) / maximum * (bottom - top)
                    segment.extend((x, y))
                    previous_stamp = stamp
                draw_segment()
            if self.state != "온라인":
                canvas.create_text((left + right) / 2, top + 14, text=self.state + " · 이전 측정 기록", fill="#b45309")
