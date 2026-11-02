"""Простой локальный интерфейс для существующего загрузчика KetabOnline."""

from __future__ import annotations

import os
import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
from gui_i18n import LANGUAGES, TEXT
from gui_runtime import data_directory, worker_command

ROOT = Path(__file__).resolve().parent
PROGRESS = re.compile(r"Saved fragment .*?\((\d+)/(\d+)\)")
DATA_DIR = data_directory()
PREFERENCES = DATA_DIR / ".gui-preferences.json"
THEMES = {
    "light": {"bg": "#f1f4f8", "card": "#ffffff", "text": "#17243a", "muted": "#68778d", "field": "#ffffff", "button": "#edf2fa", "button_text": "#244568", "active": "#d9e6f7", "accent": "#2464d8", "accent_active": "#1948aa", "disabled": "#a3adba", "trough": "#e4eaf3", "log": "#f6f8fc"},
    "dark": {"bg": "#101827", "card": "#1b293b", "text": "#edf4ff", "muted": "#adbed2", "field": "#293a50", "button": "#30445c", "button_text": "#edf4ff", "active": "#415c7d", "accent": "#467de5", "accent_active": "#6596ee", "disabled": "#8595a8", "trough": "#34465c", "log": "#101c2b"},
    "sepia": {"bg": "#eee7da", "card": "#fffaf0", "text": "#3f3024", "muted": "#776452", "field": "#fffdf8", "button": "#efe3d1", "button_text": "#5c412c", "active": "#e5d1b5", "accent": "#8a5436", "accent_active": "#6f4029", "disabled": "#ad9d8a", "trough": "#e4d8c7", "log": "#f8f0e3"},
}


def load_preferences(path: Path = PREFERENCES) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_language(path: Path = PREFERENCES) -> str:
    value = load_preferences(path).get("language")
    return value if isinstance(value, str) and value in LANGUAGES else "Русский"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Скачиватель книг — KetabOnline")
        self.geometry("920x760")
        self.minsize(760, 680)
        self.configure(background="#f1f4f8")
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stopping = False
        self.source = tk.StringVar()
        self.output = tk.StringVar(value=str(DATA_DIR / "output"))
        self.delay = tk.StringVar(value="1.5")
        self.workers = tk.StringVar(value="2")
        self.mode = tk.StringVar(value="test")
        self.language = tk.StringVar(value=load_language())
        saved_theme = load_preferences().get("theme", "light")
        self.theme_id = saved_theme if isinstance(saved_theme, str) and saved_theme in THEMES else "light"
        self.theme_name = tk.StringVar()
        self.timeout = tk.StringVar(value="30")
        self.retries = tk.StringVar(value="4")
        self.status_key = "ready"
        self.status_args: dict[str, object] = {}
        self.status = tk.StringVar()
        self.translated_widgets: list[tuple[tk.Widget, str]] = []
        self.mode_help = tk.StringVar()
        self.mode.trace_add("write", self.update_mode_help)
        self.update_mode_help()
        self._build()
        self.apply_theme()
        self.set_status("ready")
        self.change_language()
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.after(100, self.poll_messages)

    def tr(self, key: str, **values: object) -> str:
        return TEXT[LANGUAGES[self.language.get()]][key].format(**values)

    def bind_text(self, widget: tk.Widget, key: str) -> tk.Widget:
        self.translated_widgets.append((widget, key))
        widget.configure(text=self.tr(key))
        return widget

    def set_status(self, key: str, **values: object) -> None:
        self.status_key, self.status_args = key, values
        self.status.set(self.tr(key, **values))

    def change_language(self, _event: object = None) -> None:
        for widget, key in self.translated_widgets:
            widget.configure(text=self.tr(key))
        self.title(self.tr("title") + " — KetabOnline")
        self.update_mode_help()
        self.set_status(self.status_key, **self.status_args)
        self.refresh_theme_options()
        self.apply_direction(LANGUAGES[self.language.get()] == "ar")
        if _event is not None:
            self.save_preferences()

    def save_preferences(self) -> None:
        try:
            PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
            PREFERENCES.write_text(json.dumps({"language": self.language.get(), "theme": self.theme_id}, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            messagebox.showwarning(self.tr("preferences_error"), str(exc), parent=self)

    def refresh_theme_options(self) -> None:
        self.theme_box.configure(values=[self.tr("theme_" + key) for key in THEMES])
        self.theme_name.set(self.tr("theme_" + self.theme_id))

    def change_theme(self, _event: object = None) -> None:
        for key in THEMES:
            if self.theme_name.get() == self.tr("theme_" + key):
                self.theme_id = key
                self.apply_theme()
                self.save_preferences()
                return

    def apply_theme(self) -> None:
        c = THEMES[self.theme_id]
        style = ttk.Style(self)
        self.configure(background=c["bg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["card"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("HeaderMuted.TLabel", background=c["bg"], foreground=c["muted"])
        style.configure("Card.TLabel", background=c["card"], foreground=c["text"])
        style.configure("Muted.TLabel", background=c["card"], foreground=c["muted"])
        style.configure("Section.TLabel", background=c["card"], foreground=c["text"])
        style.configure("TButton", background=c["button"], foreground=c["button_text"])
        style.map("TButton", background=[("disabled", c["card"]), ("active", c["active"])], foreground=[("disabled", c["disabled"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("disabled", c["disabled"]), ("active", c["accent_active"])], foreground=[("disabled", "#ffffff")])
        style.configure("TRadiobutton", background=c["card"], foreground=c["text"])
        style.map("TRadiobutton", background=[("active", c["card"])], foreground=[("disabled", c["disabled"])])
        style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"], insertcolor=c["text"])
        style.configure("TCombobox", fieldbackground=c["field"], foreground=c["text"], background=c["button"], arrowcolor=c["text"])
        style.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor=c["trough"], background=c["accent"])
        self.log.configure(background=c["log"], foreground=c["text"], insertbackground=c["text"])

    def apply_direction(self, rtl: bool) -> None:
        """Mirror packed controls while keeping paths and URLs left-to-right."""
        def visit(parent: tk.Widget) -> None:
            for widget in parent.winfo_children():
                if widget.winfo_manager() == "pack":
                    if not hasattr(widget, "_original_pack"):
                        info = widget.pack_info()
                        widget._original_pack = (info["side"], info["anchor"])  # type: ignore[attr-defined]
                    side, anchor = widget._original_pack  # type: ignore[attr-defined]
                    if rtl:
                        side = {"left": "right", "right": "left"}.get(side, side)
                        anchor = {"w": "e", "e": "w", "nw": "ne", "ne": "nw"}.get(anchor, anchor)
                    widget.pack_configure(side=side, anchor=anchor)
                visit(widget)
        visit(self)

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f1f4f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f1f4f8", foreground="#17243a", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#17243a", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#68778d", font=("Segoe UI", 9))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#17243a", font=("Segoe UI", 11, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8), background="#edf2fa", foreground="#244568", borderwidth=0)
        style.map("TButton", background=[("disabled", "#f0f2f5"), ("active", "#d9e6f7")], foreground=[("disabled", "#a3adba")])
        style.configure("Accent.TButton", background="#2464d8", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(18, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("disabled", "#aebbd2"), ("active", "#1948aa")], foreground=[("disabled", "#ffffff")])
        style.configure("TRadiobutton", background="#ffffff", foreground="#17243a", font=("Segoe UI", 10))
        style.map("TRadiobutton", background=[("active", "#ffffff")])
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=5)
        style.configure("Horizontal.TProgressbar", troughcolor="#e4eaf3", background="#3475df", borderwidth=0, thickness=9)

        frame = ttk.Frame(self, padding=(22, 18))
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        lang_frame = ttk.Frame(header)
        lang_frame.pack(side="right", anchor="ne")
        self.bind_text(ttk.Label(lang_frame, style="HeaderMuted.TLabel"), "language").pack(anchor="w")
        language_box = ttk.Combobox(lang_frame, textvariable=self.language, values=list(LANGUAGES), state="readonly", width=14)
        language_box.pack(pady=(4, 0))
        language_box.bind("<<ComboboxSelected>>", self.change_language)
        theme_frame = ttk.Frame(header)
        theme_frame.pack(side="right", anchor="ne", padx=(0, 14))
        self.bind_text(ttk.Label(theme_frame, style="HeaderMuted.TLabel"), "theme").pack(anchor="w")
        self.theme_box = ttk.Combobox(theme_frame, textvariable=self.theme_name, state="readonly", width=12)
        self.theme_box.pack(pady=(4, 0))
        self.theme_box.bind("<<ComboboxSelected>>", self.change_theme)
        self.bind_text(ttk.Label(header, style="Title.TLabel"), "title").pack(anchor="w")
        self.bind_text(ttk.Label(header, style="HeaderMuted.TLabel"), "subtitle").pack(anchor="w", pady=(0, 14))

        card = ttk.Frame(frame, style="Card.TFrame", padding=16)
        card.pack(fill="x", pady=(0, 10))
        self.bind_text(ttk.Label(card, style="Section.TLabel"), "book").pack(anchor="w", pady=(0, 8))
        self.bind_text(ttk.Label(card, style="Card.TLabel"), "source").pack(anchor="w")
        source_entry = ttk.Entry(card, textvariable=self.source)
        source_entry.pack(fill="x", pady=(5, 2), ipady=2)
        self.bind_text(ttk.Label(card, style="Muted.TLabel"), "source_hint").pack(anchor="w", pady=(0, 6))
        source_entry.focus_set()
        self.bind_text(ttk.Label(card, style="Card.TLabel"), "folder").pack(anchor="w")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(5, 0))
        ttk.Entry(row, textvariable=self.output).pack(side="left", fill="x", expand=True)
        self.bind_text(ttk.Button(row, command=self.choose_output), "choose").pack(side="left", padx=(8, 0))
        self.bind_text(ttk.Button(row, command=self.open_output), "open").pack(side="left", padx=(8, 0))

        options = ttk.Frame(frame, style="Card.TFrame", padding=16)
        options.pack(fill="x", pady=(0, 10))
        self.bind_text(ttk.Label(options, style="Section.TLabel"), "settings").pack(anchor="w", pady=(0, 8))
        modes = ttk.Frame(options, style="Card.TFrame")
        modes.pack(fill="x")
        for mode in ("test", "full", "validate", "export"):
            self.bind_text(ttk.Radiobutton(modes, variable=self.mode, value=mode), mode).pack(side="left", padx=(0, 14))
        ttk.Label(options, textvariable=self.mode_help, style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        settings = ttk.Frame(options, style="Card.TFrame")
        settings.pack(fill="x")
        self.bind_text(ttk.Label(settings, style="Card.TLabel"), "delay").pack(side="left")
        ttk.Entry(settings, textvariable=self.delay, width=7).pack(side="left", padx=(6, 18))
        self.bind_text(ttk.Label(settings, style="Card.TLabel"), "workers").pack(side="left")
        ttk.Combobox(settings, textvariable=self.workers, values=("1", "2", "3", "4"), state="readonly", width=4).pack(side="left", padx=6)
        self.bind_text(ttk.Label(settings, style="Card.TLabel"), "timeout").pack(side="left", padx=(12, 4))
        ttk.Entry(settings, textvariable=self.timeout, width=5).pack(side="left")
        self.bind_text(ttk.Label(settings, style="Card.TLabel"), "retries").pack(side="left", padx=(12, 4))
        ttk.Entry(settings, textvariable=self.retries, width=4).pack(side="left")
        self.bind_text(ttk.Label(options, style="Muted.TLabel"), "speed_hint").pack(anchor="w", pady=(7, 0))

        work = ttk.Frame(frame, style="Card.TFrame", padding=16)
        work.pack(fill="both", expand=True)
        actions = ttk.Frame(work, style="Card.TFrame")
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, command=self.start, style="Accent.TButton")
        self.bind_text(self.start_button, "start")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, command=self.stop, state="disabled")
        self.bind_text(self.stop_button, "stop")
        self.stop_button.pack(side="left", padx=8)
        self.bind_text(ttk.Button(actions, command=self.show_guide), "guide").pack(side="left", padx=4)
        ttk.Label(actions, textvariable=self.status, style="Card.TLabel").pack(side="right", padx=4)
        self.progress = ttk.Progressbar(work, mode="determinate")
        self.progress.pack(fill="x", pady=10)
        self.bind_text(ttk.Label(work, style="Section.TLabel"), "log").pack(anchor="w")
        self.bind_text(ttk.Label(work, style="Muted.TLabel"), "log_hint").pack(anchor="w")
        log_frame = ttk.Frame(work, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(log_frame, wrap="word", state="disabled", font=("Consolas", 9),
                           background="#f6f8fc", foreground="#26364e", relief="flat", padx=10, pady=10)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def update_mode_help(self, *_args: object) -> None:
        self.mode_help.set(self.tr(self.mode.get() + "_help"))

    def show_guide(self) -> None:
        messagebox.showinfo(self.tr("guide"), self.tr("guide_text"), parent=self)

    def translate_log(self, line: str) -> str:
        prefix_match = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \w+ )(.+?)(\n?)$", line)
        if not prefix_match:
            return line
        prefix, message, ending = prefix_match.groups()
        patterns = (
            (r"GET (.+) \(attempt (\d+)/(\d+)\)", "log_fetch", ("url", "attempt", "total")),
            (r"Saved fragment (.+) \((\d+)/(\d+)\)", "log_fragment", ("fragment", "current", "total")),
            (r"Checked requested page (\d+); parsed (\d+) embedded page\(s\)", "log_checked", ("page", "count")),
            (r"Downloader workers: (\d+)", "log_workers", ("count",)),
            (r"Retrying in ([\d.]+) seconds", "log_retry", ("seconds",)),
            (r"Test saved (\d+) control fragments", "log_sample", ("count",)),
            (r"Part (\d+) \(.+?\): global pages (\d+)-(\d+), (\d+) available pages, (\d+) TOC entries", "log_volume", ("part", "start", "end", "count", "toc")),
        )
        fixed = {"Используется сохранённая карта книги; повторное исследование не требуется": "log_map", "Полная выгрузка завершена и экспортирована": "log_complete"}
        if message in fixed:
            return prefix + self.tr(fixed[message]) + ending
        for pattern, key, names in patterns:
            match = re.fullmatch(pattern, message)
            if match:
                return prefix + self.tr(key, **dict(zip(names, match.groups()))) + ending
        return line

    def choose_output(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output.get(), title=self.tr("folder"))
        if path:
            self.output.set(path)

    def open_output(self) -> None:
        path = Path(self.output.get()).expanduser()
        if not path.is_dir():
            messagebox.showinfo(self.tr("folder"), self.tr("missing_folder"))
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(self.tr("open_error"), str(exc))

    def start(self) -> None:
        source = self.source.get().strip()
        if source.isdecimal() and int(source) > 0:
            source_args = ["--book-id", source]
        else:
            try:
                url = urlparse(source)
                valid = url.scheme in ("http", "https") and url.hostname in ("ketabonline.com", "www.ketabonline.com") and re.search(r"/books/\d+(/|$)", url.path)
            except ValueError:
                valid = False
            if not valid:
                messagebox.showerror(self.tr("invalid"), self.tr("source_error"))
                return
            source_args = ["--url", source]
        try:
            delay = float(self.delay.get().replace(",", "."))
            if not 0 <= delay <= 3600:
                raise ValueError
            workers = int(self.workers.get())
            timeout = float(self.timeout.get().replace(",", "."))
            retries = int(self.retries.get())
            if workers not in range(1, 5) or not 1 <= timeout <= 3600 or retries not in range(21) or not self.output.get().strip():
                raise ValueError
        except ValueError:
            messagebox.showerror(self.tr("invalid"), self.tr("settings_error"))
            return
        command = [*worker_command(), *source_args,
                   "--output-dir", str(Path(self.output.get()).expanduser().resolve()),
                   "--delay", str(delay), "--workers", str(workers),
                   "--timeout", str(timeout), "--max-retries", str(retries)]
        if self.mode.get() == "test":
            command.append("--test")
        elif self.mode.get() == "export":
            command.append("--export")
        elif self.mode.get() == "validate":
            command.append("--validate-only")
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            self.process = subprocess.Popen(command, cwd=DATA_DIR, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            encoding="utf-8", errors="replace", env=env,
                                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except OSError as exc:
            messagebox.showerror(self.tr("start_error"), str(exc))
            return
        self.stopping = False
        self.progress["value"] = 0
        self.set_status("running")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.append_log(f"\n--- {self.tr('log_start')} ---\n")
        threading.Thread(target=self.read_process, args=(self.process,), daemon=True).start()

    def read_process(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(("line", line))
        process.stdout.close()
        self.messages.put(("done", process.wait()))

    def poll_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "line":
                    line = str(value)
                    self.append_log(self.translate_log(line))
                    match = PROGRESS.search(line)
                    if match:
                        current, total = map(int, match.groups())
                        self.progress.configure(maximum=total, value=current)
                        self.set_status("saved", current=current, total=total)
                else:
                    if self.stopping:
                        self.set_status("stopped")
                    elif value == 0:
                        self.set_status("done")
                    else:
                        self.set_status("error_code", code=value)
                    self.append_log(f"--- {self.status.get()} ---\n")
                    self.process = None
                    self.start_button.config(state="normal")
                    self.stop_button.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.poll_messages)

    def append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.stopping = True
            self.set_status("stopping")
            self.stop_button.config(state="disabled")
            self.process.terminate()

    def close_window(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno(self.tr("close_title"), self.tr("close_text")):
                return
            self.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
