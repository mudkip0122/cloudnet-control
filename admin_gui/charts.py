"""Bounded per-PC history and rate-limited Matplotlib charts on the Tk thread."""
from collections import deque
from datetime import datetime, timezone
import math
import time
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.dates import AutoDateLocator, DateFormatter, date2num
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator, PercentFormatter

from admin_gui.theme import BLUE, BORDER, MUTED, WHITE

FIELDS = ("cpu_percent", "ram_percent", "upload_mbps", "download_mbps")


def metric_value(value, percent=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value if not percent or value <= 100 else None


class MetricHistory:
    def __init__(self, limit=180):
        if type(limit) is not int or limit < 1:
            raise ValueError("History limit must be a positive integer")
        self.limit = limit
        self.samples = {}
        self.interrupted = set()

    def disconnect(self):
        self.interrupted.update(self.samples)

    def update(self, devices):
        self.samples = {key: value for key, value in self.samples.items() if key in devices}
        self.interrupted.intersection_update(devices)
        for pc_id, device in devices.items():
            metrics = device.get("metrics") or {}
            if not device.get("online") or not metrics.get("timestamp"):
                self.interrupted.add(pc_id)
                continue
            try:
                stamp = datetime.fromisoformat(metrics["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    continue
                seconds = stamp.timestamp()
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
            points = self.samples.setdefault(pc_id, deque(maxlen=self.limit))
            if points and seconds <= points[-1][0]:
                continue
            values = {field: metric_value(metrics.get(field), field in FIELDS[:2]) for field in FIELDS}
            values["timestamp"] = metrics["timestamp"]
            values["_gap_before"] = pc_id in self.interrupted
            self.interrupted.discard(pc_id)
            points.append((seconds, values))


def series(points, field):
    """NaNs break missing data and intervals over six seconds; zero stays zero."""
    x, y, previous = [], [], None
    for stamp, metrics in points:
        moment = date2num(datetime.fromtimestamp(stamp, timezone.utc))
        if previous is not None and (stamp - previous > 6 or metrics.get("_gap_before")):
            x.append(moment)
            y.append(float("nan"))
        x.append(moment)
        value = metrics.get(field)
        y.append(value if value is not None else float("nan"))
        previous = stamp
    return x, y


class LiveCharts(ttk.Frame):
    def __init__(self, parent, historical=False):
        super().__init__(parent)
        self.historical = historical
        self.axes = []
        self.plots = []
        self.lines = {}
        self.legend_values = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure((0, 1), weight=1, uniform="charts")
        for row, (title, fields) in enumerate((
                ("CPU · RAM 사용률", (("cpu_percent", "CPU", BLUE), ("ram_percent", "RAM", "#9333ea"))),
                ("네트워크 전송량", (("upload_mbps", "업로드", "#00a994"), ("download_mbps", "다운로드", "#ec9900"))))):
            card = ttk.Frame(self, style="Card.TFrame", padding=(12, 7))
            card.grid(row=row, column=0, sticky="nsew", pady=(0, 8) if row == 0 else 0)
            heading = ttk.Frame(card, style="Surface.TFrame")
            heading.pack(fill="x")
            ttk.Label(heading, text=title, style="CardTitle.TLabel").pack(side="left", padx=6)
            figure = Figure(figsize=(5, 1.25), dpi=100, facecolor=WHITE)
            figure.subplots_adjust(left=.095, right=.975, top=.93, bottom=.24)
            axis = figure.add_subplot()
            self.axes.append(axis)
            for field, label, color in reversed(fields):
                legend = ttk.Frame(heading, style="Surface.TFrame")
                legend.pack(side="right", padx=7)
                ttk.Label(legend, text="━", foreground=color, style="Surface.TLabel").pack(side="left", padx=(0, 4))
                value = ttk.Label(legend, text=label, style="Surface.TLabel")
                value.pack(side="left")
                if row:
                    self.legend_values[field] = (value, label)
            for field, label, color in fields:
                self.lines[field], = axis.plot([], [], color=color, linewidth=1.7, marker=".", markersize=2.5)
            axis.grid(True, color=BORDER, linewidth=.7)
            axis.set_axisbelow(True)
            for spine in axis.spines.values():
                spine.set_color(BORDER)
            axis.tick_params(colors=MUTED, labelsize=8)
            axis.xaxis.set_major_locator(AutoDateLocator(tz=timezone.utc, minticks=3, maxticks=7))
            axis.xaxis.set_major_formatter(DateFormatter("%H:%M:%S", tz=timezone.utc))
            axis.set_xlabel("UTC", loc="right", labelpad=0, color=MUTED, fontsize=8)
            plot = FigureCanvasTkAgg(figure, master=card)
            plot.get_tk_widget().pack(fill="both", expand=True)
            self.plots.append(plot)
        self.axes[0].set_ylim(0, 100)
        self.axes[0].yaxis.set_major_locator(MultipleLocator(25))
        self.axes[0].yaxis.set_major_formatter(PercentFormatter(100))
        self.axes[1].set_ylabel("Mbps", color=MUTED, fontsize=8)
        self.notice = ttk.Label(self, text="PC를 선택하세요", style="Muted.TLabel")
        self.notice.grid(row=2, column=0, sticky="w")
        self.canvas = self.plots[0].get_tk_widget()
        self.points, self.state, self.pc_id = (), "PC를 선택하세요", None
        self.signature = None
        self.last_draw = float("-inf")
        self.pending_draw = None
        self.bind("<Destroy>", self.on_destroy, add=True)

    def show(self, points, state, pc_id=None):
        self.points = tuple(points)
        signature = (pc_id, state, tuple((stamp, tuple(metrics.get(k) for k in (*FIELDS, "_gap_before")))
                                         for stamp, metrics in self.points))
        if signature == self.signature:
            return
        immediate = pc_id != self.pc_id or state != self.state
        self.pc_id, self.state, self.signature = pc_id, state, signature
        if state == "온라인" or self.historical:
            self.notice.grid_remove()
        else:
            suffix = " · 이전 측정 기록" if self.points else ""
            self.notice.configure(text=state + suffix)
            self.notice.grid()
        elapsed = time.monotonic() - self.last_draw
        if immediate or elapsed >= 1:
            self.draw()
        elif self.pending_draw is None:
            self.pending_draw = self.after(max(1, int((1 - elapsed) * 1000)), self.draw)

    def draw(self):
        if self.pending_draw is not None:
            self.after_cancel(self.pending_draw)
            self.pending_draw = None
        for field, line in self.lines.items():
            line.set_data(*series(self.points, field))
        if self.points:
            first, last = self.points[0][0], self.points[-1][0]
            if first == last:
                first, last = first - 2, last + 2
            limits = [date2num(datetime.fromtimestamp(stamp, timezone.utc)) for stamp in (first, last)]
        else:
            limits = (0, 1)
        for axis in self.axes:
            axis.set_xlim(*limits)
            axis.tick_params(axis="x", labelbottom=bool(self.points))
        traffic = [m[field] for _, m in self.points for field in FIELDS[2:] if m.get(field) is not None]
        self.axes[1].set_ylim(0, max(1, max(traffic, default=0) * 1.15))
        for field, (widget, label) in self.legend_values.items():
            value = self.points[-1][1].get(field) if self.points and self.state == "온라인" else None
            widget.configure(text=f"{label} {value:.2f} Mbps" if value is not None else label)
        self.last_draw = time.monotonic()
        for plot in self.plots:
            plot.draw_idle()

    def on_destroy(self, event):
        if event.widget is self:
            if self.pending_draw is not None:
                self.after_cancel(self.pending_draw)
                self.pending_draw = None
            for plot in self.plots:
                if plot._idle_draw_id:
                    self.after_cancel(plot._idle_draw_id)
                    plot._idle_draw_id = None
                plot.figure.clear()
