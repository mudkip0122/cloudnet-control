"""Shared desktop styles for the dashboard and its windows."""
import tkinter as tk
from tkinter import ttk

NAVY = "#102750"
BACKGROUND = "#edf2f8"
WHITE = "#ffffff"
INK = "#101828"
MUTED = "#475569"
BORDER = "#ced7e4"
BLUE = "#075af5"
SELECTED = "#e3edfb"
GREEN = "#087a30"
RED = "#b42318"
FONT = "Malgun Gothic"


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 10), foreground=INK, background=BACKGROUND)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Section.TLabel", font=(FONT, 13, "bold"))
    style.configure("Title.TLabel", font=(FONT, 25, "bold"))
    style.configure("Card.TFrame", background=WHITE, bordercolor=BORDER, borderwidth=1, relief="solid")
    style.configure("Surface.TFrame", background=WHITE)
    style.configure("Online.TLabel", background=GREEN, foreground=WHITE, font=(FONT, 10, "bold"))
    style.configure("Surface.TLabel", background=WHITE)
    style.configure("CardTitle.TLabel", background=WHITE, font=(FONT, 13, "bold"))
    style.configure("Value.TLabel", background=WHITE, font=(FONT, 27, "bold"))
    style.configure("Header.TFrame", background=NAVY)
    style.configure("Header.TLabel", background=NAVY, foreground=WHITE)
    style.configure("HeaderTitle.TLabel", background=NAVY, foreground=WHITE, font=(FONT, 19, "bold"))
    style.configure("TButton", background=WHITE, bordercolor=BORDER, padding=(12, 7),
                    font=(FONT, 10, "bold"))
    style.map("TButton", background=[("disabled", BACKGROUND), ("pressed", SELECTED), ("active", SELECTED)],
              foreground=[("disabled", "#68778d")])
    for name, color in (("Primary.TButton", BLUE), ("Header.TButton", NAVY), ("Danger.TButton", WHITE)):
        foreground = RED if name == "Danger.TButton" else WHITE
        active = "#feeceb" if name == "Danger.TButton" else "#234679"
        style.configure(name, background=color, foreground=foreground,
                        bordercolor=RED if name == "Danger.TButton" else BORDER)
        style.map(name, background=[("disabled", BACKGROUND), ("pressed", active), ("active", active)],
                  foreground=[("disabled", "#68778d"), ("!disabled", foreground)])
    style.configure("TMenubutton", background=WHITE, bordercolor=RED,
                    foreground=RED, padding=(12, 7), font=(FONT, 10, "bold"))
    style.map("TMenubutton", background=[("active", "#feeceb")], foreground=[("disabled", MUTED)])
    style.configure("TEntry", fieldbackground=WHITE, bordercolor=BORDER, padding=6)
    style.configure("TCombobox", fieldbackground=WHITE, bordercolor=BORDER, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", WHITE)])
    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, bordercolor=BORDER, rowheight=32)
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", INK)])
    style.configure("Treeview.Heading", background="#f1f5fb", bordercolor=BORDER,
                    lightcolor="#f1f5fb", darkcolor=BORDER, padding=(8, 8), font=(FONT, 10, "bold"))
    for kind in ("Vertical", "Horizontal"):
        style.configure(f"{kind}.TScrollbar", background=BORDER, troughcolor=BACKGROUND,
                        bordercolor=BACKGROUND, arrowcolor=MUTED, arrowsize=11)
    style.configure("PC.Treeview", rowheight=54, font=(FONT, 10))
    style.configure("PC.Treeview.Heading", background=WHITE)
    style.configure("TNotebook", background=BACKGROUND, bordercolor=BORDER)
    style.configure("TNotebook.Tab", padding=(16, 8), background=BACKGROUND)
    style.map("TNotebook.Tab", background=[("selected", WHITE)], foreground=[("selected", BLUE)])
    style.map("Fixed.TNotebook.Tab", expand=[("selected", (0, 0, 0, 0)), ("!selected", (0, 0, 0, 0))],
              padding=[("selected", (16, 8)), ("!selected", (16, 8))],
              background=[("selected", WHITE)], foreground=[("selected", BLUE)])
    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER)
    style.configure("TLabelframe.Label", font=(FONT, 12, "bold"))


def header(parent, title):
    bar = ttk.Frame(parent, style="Header.TFrame", padding=(20, 12))
    bar.pack(fill="x")
    ttk.Label(bar, text=title, style="HeaderTitle.TLabel").pack(side="left")
    return bar


def window(parent, title, geometry, minimum):
    child = tk.Toplevel(parent, background=BACKGROUND)
    child.title(title)
    child.geometry(geometry)
    child.minsize(*minimum)
    return child
