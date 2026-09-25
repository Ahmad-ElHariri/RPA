"""Double-click to inspect Airtable pages or run registered workflows."""

from pathlib import Path
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from backend.airtable_actions import list_actions
from backend.config import Settings, load_apps, _validate_airtable_url


ROOT = Path(__file__).resolve().parent


def build_arguments(mode: str, function: str, target: str, url: str) -> list[str]:
    if mode not in {"Inspect", "Run"}:
        raise ValueError("Choose Inspect or Run.")
    if target not in {"One URL", "All apps in apps.json"}:
        raise ValueError("Choose One URL or All apps.")
    if mode == "Inspect":
        return ["inspect", "--url", _validate_airtable_url(url, "Inspection URL")]
    if function not in list_actions():
        raise ValueError("Choose an available function.")
    args = ["run", function]
    if target == "One URL":
        args += ["--url", _validate_airtable_url(url, "Run URL")]
    return args


class Launcher:
    def __init__(self, window: tk.Tk) -> None:
        self.window = window
        window.title("Airtable automation")
        window.geometry("820x650")
        window.minsize(680, 600)
        window.tk.call("tk", "scaling", 96 / 72)
        window.configure(background="#f3f5fa")
        window.option_add("*Font", ("Segoe UI", 10))
        self.events = queue.Queue()
        self.busy = False
        self.mode = tk.StringVar(value="Inspect")
        self.function = tk.StringVar()
        self.target = tk.StringVar(value="One URL")
        self.url = tk.StringVar()
        self.status = tk.StringVar(value="Ready when you are")
        self.artifacts = {"inspection": None, "report": None}
        self.settings = Settings.from_env()

        # 1. A consistent theme keeps controls, cards, and activity states readable.
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5fa")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#26324b")
        style.configure("Muted.TLabel", foreground="#69758c", font=("Segoe UI", 9))
        style.configure("Field.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", padding=9, fieldbackground="#f8f9fc", bordercolor="#dce2ee", lightcolor="#dce2ee", darkcolor="#dce2ee")
        style.configure("TCombobox", padding=8, fieldbackground="#f8f9fc", background="#eef1f8", bordercolor="#dce2ee", arrowcolor="#63708b")
        style.map("TCombobox", fieldbackground=[("readonly", "#f8f9fc"), ("disabled", "#eef1f5")], foreground=[("disabled", "#8993a7")])
        style.configure("Primary.TButton", background="#6554d9", foreground="#ffffff", padding=(24, 11), borderwidth=0, font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#d5d2ee"), ("active", "#5141bf")], foreground=[("disabled", "#88819f")])
        style.configure("Link.TButton", background="#edf0fc", foreground="#5141bf", padding=(14, 9), borderwidth=0)
        style.map("Link.TButton", background=[("active", "#e0e5fa")], foreground=[("disabled", "#9aa3b7")])
        style.configure("Mode.TRadiobutton", background="#eef1f8", foreground="#62708a", padding=(22, 10), indicatoron=False, font=("Segoe UI", 10, "bold"))
        style.map("Mode.TRadiobutton", background=[("selected", "#6554d9"), ("active", "#e4e8f5")], foreground=[("selected", "white"), ("disabled", "#9aa3b7")])
        style.configure("Activity.Horizontal.TProgressbar", background="#6554d9", troughcolor="#e8eaf5", borderwidth=0)

        # 2. Configure the operation in a compact card above the activity panel.
        body = ttk.Frame(window, padding=(24, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        card = ttk.Frame(body, style="Card.TFrame", padding=20)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        modes = ttk.Frame(card, style="Card.TFrame")
        modes.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 15))
        self.mode_buttons = []
        for mode in ("Inspect", "Run"):
            control = ttk.Radiobutton(modes, text=mode, value=mode, variable=self.mode, command=self.refresh, style="Mode.TRadiobutton")
            control.pack(side="left", padx=(0, 4))
            self.mode_buttons.append(control)

        ttk.Label(card, text="Function", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 18))
        self.function_box = ttk.Combobox(card, textvariable=self.function, values=list_actions(), state="readonly")
        self.function_box.grid(row=1, column=1, sticky="ew", pady=5)
        if list_actions():
            self.function.set(list_actions()[0])
        ttk.Label(card, text="Target", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        self.target_box = ttk.Combobox(card, textvariable=self.target, state="readonly")
        self.target_box.grid(row=2, column=1, sticky="ew", pady=5)
        self.target_box.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Label(card, text="Airtable URL", style="Field.TLabel").grid(row=3, column=0, sticky="w")
        self.url_entry = ttk.Entry(card, textvariable=self.url)
        self.url_entry.grid(row=3, column=1, sticky="ew", pady=5)
        self.button = ttk.Button(modes, text="Inspect page", command=self.start, style="Primary.TButton")
        self.button.pack(side="right")

        activity = ttk.Frame(body, style="Card.TFrame", padding=18)
        activity.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        top = ttk.Frame(activity, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Logs", font=("Segoe UI", 11, "bold")).pack(side="left")
        self.status_label = ttk.Label(top, textvariable=self.status, style="Muted.TLabel")
        self.status_label.pack(side="right")
        self.progress = ttk.Progressbar(activity, mode="indeterminate", style="Activity.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 10))
        self.output = ScrolledText(activity, height=7, state="disabled", wrap="word", background="#17243e", foreground="#dbe4f4", insertbackground="white", font=("Consolas", 9), relief="flat", borderwidth=0, padx=12, pady=10)
        self.output.pack(fill="both", expand=True)

        # 3. Open only files announced by this operation, once they exist on disk.
        links = ttk.Frame(body, style="Card.TFrame", padding=(18, 12))
        links.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.inspection_button = ttk.Button(links, text="Open inspection", style="Link.TButton", state="disabled", command=lambda: self.open_artifact("inspection"))
        self.inspection_button.pack(side="left", padx=(0, 8))
        self.report_button = ttk.Button(links, text="Open report", style="Link.TButton", state="disabled", command=lambda: self.open_artifact("report"))
        self.report_button.pack(side="left")
        try:
            self.url.set(load_apps(self.settings.apps_file)[0].url)
        except ValueError as exc:
            self.append(f"Settings: {exc}\n")
        self.refresh()
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.poll)

    def refresh(self, event=None) -> None:
        inspect = self.mode.get() == "Inspect"
        if inspect:
            self.target.set("One URL")
        self.target_box.configure(values=("One URL",) if inspect else ("One URL", "All apps in apps.json"))
        for control in self.mode_buttons:
            control.configure(state="disabled" if self.busy else "normal")
        self.function_box.configure(state="disabled" if self.busy or inspect else "readonly")
        self.target_box.configure(state="disabled" if self.busy or inspect else "readonly")
        self.url_entry.configure(state="disabled" if self.busy or self.target.get() != "One URL" else "normal")
        self.button.configure(text="Inspect page" if inspect else "Run workflow", state="disabled" if self.busy else "normal")


    def append(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def start(self) -> None:
        if self.busy:
            return
        try:
            args = build_arguments(self.mode.get(), self.function.get(), self.target.get(), self.url.get())
        except ValueError as exc:
            messagebox.showerror("Check your selection", str(exc), parent=self.window)
            return
        self.artifacts = {"inspection": None, "report": None}
        self.update_links()
        self.busy = True
        self.progress.start(12)
        self.status_label.configure(foreground="#6554d9")
        self.status.set(f"{self.mode.get()} in progress...")
        self.append("\nStarting " + self.mode.get().lower() + "...\n")
        self.refresh()
        threading.Thread(target=self.execute, args=(args,), daemon=True).start()

    def execute(self, args: list[str]) -> None:
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        try:
            with subprocess.Popen(
                [str(executable), "-u", str(ROOT / "main.py"), *args],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as process:
                for line in process.stdout:
                    self.events.put(("output", line))
                self.events.put(("done", process.wait()))
        except Exception as exc:
            self.events.put(("output", f"Could not start: {exc}\n"))
            self.events.put(("done", 1))

    def poll(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "output":
                self.append(value)
                self.track_artifact(value)
            else:
                self.busy = False
                self.progress.stop()
                self.status_label.configure(foreground="#20835d" if value == 0 else "#bd4352")
                self.status.set("Finished successfully" if value == 0 else "Failed or incomplete — review output and report")
                self.refresh()
        self.update_links()
        self.window.after(100, self.poll)

    def track_artifact(self, line: str) -> None:
        markers = (("inspection", "Inspection file: "), ("report", "Excel report: "))
        for kind, marker in markers:
            if marker not in line:
                continue
            candidate = Path(line.split(marker, 1)[1].strip()).resolve()
            directory = (self.settings.inspections_dir if kind == "inspection" else self.settings.reports_dir).resolve()
            suffix = ".json" if kind == "inspection" else ".xlsx"
            if candidate.parent == directory and candidate.suffix.lower() == suffix:
                self.artifacts[kind] = candidate

    def update_links(self) -> None:
        for kind, button in (("inspection", self.inspection_button), ("report", self.report_button)):
            path = self.artifacts[kind]
            button.configure(state="normal" if path and path.is_file() else "disabled")

    def open_artifact(self, kind: str) -> None:
        path = self.artifacts[kind]
        if not path or not path.is_file():
            self.update_links()
            messagebox.showinfo("File unavailable", "The file has not been created yet or has been moved.", parent=self.window)
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("Could not open file", str(exc), parent=self.window)

    def close(self) -> None:
        if self.busy:
            messagebox.showinfo("Operation active", "Wait for the run to finish. To finish inspection, close the automation browser.", parent=self.window)
        else:
            self.window.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    try:
        Launcher(window)
    except Exception as exc:
        messagebox.showerror("Could not open launcher", str(exc), parent=window)
        window.destroy()
    else:
        window.mainloop()
