import logging
import tkinter as tk
from tkinter import ttk

from ..core import AIMIOS

logger = logging.getLogger(__name__)

class AIMIOSApp(tk.Tk):
    def __init__(self, app: AIMIOS) -> None:
        super().__init__()
        self.app = app
        self.title("AIMIOS")
        self.geometry("900x600")
        self._create_widgets()

    def _create_widgets(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.dashboard_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.dashboard_frame, text="Dashboard")

        self.engine_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.engine_frame, text="Engines")

        self.status_text = tk.Text(self.dashboard_frame, state="disabled", wrap="word")
        self.status_text.pack(fill="both", expand=True, padx=10, pady=10)

        self.start_button = ttk.Button(
            self.engine_frame,
            text="Start All Engines",
            command=self.start_engines,
        )
        self.start_button.pack(pady=10)

        self.stop_button = ttk.Button(
            self.engine_frame, text="Stop All Engines", command=self.stop_engines
        )
        self.stop_button.pack(pady=10)

        self.engine_list = tk.Listbox(self.engine_frame)
        self.engine_list.pack(fill="both", expand=True, padx=10, pady=10)

    def log_status(self, message: str) -> None:
        self.status_text.configure(state="normal")
        self.status_text.insert("end", message + "\n")
        self.status_text.configure(state="disabled")
        self.status_text.see("end")

    def start_engines(self) -> None:
        self.app.start_all()
        self.log_status("All engines started.")

    def stop_engines(self) -> None:
        self.app.stop_all()
        self.log_status("All engines stopped.")

    def add_engine(self, engine_name: str) -> None:
        self.engine_list.insert("end", engine_name)

    def initialize_engine_list(self) -> None:
        self.engine_list.delete(0, "end")
        for engine_name in self.app.engines:
            self.add_engine(engine_name)
