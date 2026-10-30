"""Offline wireframe using explicitly labelled sample data."""
import tkinter as tk
from tkinter import ttk


def main():
    root = tk.Tk()
    root.title("CloudNet Control — SAMPLE DATA / OFFLINE")
    root.geometry("680x350")
    ttk.Label(root, text="샘플 데이터 화면 · 서버 연결 없음", font=("", 16)).pack(pady=20)
    table = ttk.Treeview(root, columns=("field", "value"), show="headings", height=8)
    table.heading("field", text="항목")
    table.heading("value", text="선택 PC: TEAM-B-PC")
    for field, value in [("PC ID", "example-pc-b-001"), ("상태", "온라인 (샘플)"),
                         ("CPU", "23.5 %"), ("RAM", "61.2 %"), ("업로드", "1.25 Mbps"),
                         ("다운로드", "12.80 Mbps"), ("측정 시각 (UTC)", "2026-10-30 03:00:00"),
                         ("결측값 표시 예시", "—")]:
        table.insert("", "end", values=(field, value))
    table.pack(fill="x", padx=20)
    ttk.Button(root, text="종료·재부팅 (5주차 구현)", state="disabled").pack(pady=15)
    root.mainloop()


if __name__ == "__main__":
    main()
