from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
import re
import sys
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from .arxiv_api import fetch_matches, parse_arxiv_datetime
from .alerts import (
    FREQUENCIES,
    WEEKDAYS,
    AlertSettings,
    build_notification_message,
    describe_schedule,
    normalize_alert_settings,
    send_email_notification,
    should_run_now,
)
from .ai_summary import DEFAULT_MODELS, generate_daily_summary
from .models import Paper, WATCH_TYPES, WatchItem
from .paths import database_path
from .storage import Storage


class ArxivSecretaryApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("arXiv Secretary")
        self.root.geometry("1200x900")
        self.root.minsize(1200, 760)
        self._icon_image: tk.PhotoImage | None = None

        self.storage = Storage(database_path())
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.queue: Queue[tuple[str, object]] = Queue()

        self.watch_items: list[WatchItem] = []
        self.results: list[Paper] = []
        self.result_index: dict[str, Paper] = {}
        self.selected_watch_id: int | None = None
        self.status_text = tk.StringVar(value="Ready")
        self.query_mode = tk.StringVar(value="latest")
        self.limit_var = tk.StringVar(value=self.storage.get_setting("max_results", "20"))
        self.daily_days_var = tk.StringVar(value=self.storage.get_setting("daily_days", "1"))
        self.selection_count_text = tk.StringVar(value="No papers loaded yet.")
        self.last_daily_results: list[Paper] = []
        self.ai_provider_var = tk.StringVar(value=self.storage.get_setting("ai_provider", "openai"))
        self.ai_model_var = tk.StringVar()
        self.ai_key_var = tk.StringVar()
        self.ai_summary_status_text = tk.StringVar(value="Set a provider and API key, then generate a daily summary.")
        self.last_daily_summary_text = ""
        self.alert_enabled_var = tk.BooleanVar(value=True)
        self.alert_frequency_var = tk.StringVar(value="daily")
        self.alert_hour_var = tk.StringVar(value="12")
        self.alert_minute_var = tk.StringVar(value="00")
        self.alert_weekday_var = tk.StringVar(value=WEEKDAYS[0])
        self.alert_month_day_var = tk.StringVar(value="1")
        self.alert_desktop_var = tk.BooleanVar(value=True)
        self.alert_email_var = tk.BooleanVar(value=False)
        self.smtp_host_var = tk.StringVar()
        self.smtp_port_var = tk.StringVar(value="587")
        self.smtp_username_var = tk.StringVar()
        self.smtp_password_var = tk.StringVar()
        self.smtp_tls_var = tk.BooleanVar(value=True)
        self.email_from_var = tk.StringVar()
        self.email_to_var = tk.StringVar()
        self.alert_status_text = tk.StringVar(value="Alerts run while the app is open. Default schedule: daily at 12:00.")
        self.alert_next_run_text = tk.StringVar(value="")
        self.last_alert_marker = self.storage.get_setting("alert_last_run_marker", "")

        self._set_app_icon()
        self._configure_style()
        self._build_layout()
        self.ai_provider_var.trace_add("write", self._on_ai_provider_changed)
        self.alert_frequency_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_email_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_hour_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_minute_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_weekday_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_month_day_var.trace_add("write", self._on_alert_settings_changed)
        self.alert_enabled_var.trace_add("write", self._on_alert_settings_changed)
        self._load_watch_items()
        self._restore_defaults()
        self._load_ai_settings()
        self._load_alert_settings()
        self._poll_queue()
        self._poll_alert_schedule()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _resource_base_path(self) -> Path:
        bundled_base = getattr(sys, "_MEIPASS", None)
        if bundled_base:
            return Path(str(bundled_base))
        return Path(__file__).resolve().parent.parent

    def _set_app_icon(self) -> None:
        assets_dir = self._resource_base_path() / "assets"
        png_path = assets_dir / "arxiv-favicon-32x32.png"
        ico_path = assets_dir / "arxiv-favicon.ico"

        try:
            if png_path.exists():
                self._icon_image = tk.PhotoImage(file=png_path.as_posix())
                self.root.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

        if sys.platform.startswith("win"):
            try:
                if ico_path.exists():
                    self.root.iconbitmap(ico_path.as_posix())
            except Exception:
                pass

    def _configure_style(self) -> None:
        self.root.configure(bg="#f5efe3")
        self.root.option_add("*Font", "{Segoe UI} 10")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#f5efe3", foreground="#1f1b16")
        style.configure("TFrame", background="#f5efe3")
        style.configure("Panel.TFrame", background="#fbf7ee")
        style.configure("TLabel", background="#f5efe3", foreground="#1f1b16")
        style.configure("Muted.TLabel", background="#f5efe3", foreground="#73685a")
        style.configure(
            "Header.TLabel",
            background="#f5efe3",
            foreground="#1b4332",
            font=("{Georgia}", 18, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background="#fbf7ee",
            foreground="#7a3b10",
            font=("{Georgia}", 11, "bold"),
        )
        style.configure(
            "Accent.TButton",
            background="#1b6b5b",
            foreground="#ffffff",
            borderwidth=0,
            focuscolor="none",
            padding=(12, 8),
        )
        style.map("Accent.TButton", background=[("active", "#145244")])
        style.configure(
            "Secondary.TButton",
            background="#dfb96b",
            foreground="#2d2418",
            borderwidth=0,
            focuscolor="none",
            padding=(10, 7),
        )
        style.map("Secondary.TButton", background=[("active", "#ccab5c")])
        style.configure("TEntry", fieldbackground="#fffdf8", padding=6)
        style.configure("TCombobox", fieldbackground="#fffdf8", padding=4)
        style.configure(
            "Treeview",
            background="#fffdf8",
            fieldbackground="#fffdf8",
            foreground="#1f1b16",
            bordercolor="#d9cfbf",
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            background="#e9dbc5",
            foreground="#45392d",
            relief="flat",
            font=("{Segoe UI}", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#cde4d6")], foreground=[("selected", "#17372d")])

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="arXiv Secretary", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Track people, institutions, and ideas you care about. Pull a daily digest or browse the latest matches.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_text, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        feed_tab = ttk.Frame(self.notebook, padding=6)
        watch_tab = ttk.Frame(self.notebook, padding=6)
        ai_tab = ttk.Frame(self.notebook, padding=6)
        alerts_tab = ttk.Frame(self.notebook, padding=6)
        feed_tab.columnconfigure(0, weight=1)
        feed_tab.rowconfigure(0, weight=1)
        watch_tab.columnconfigure(0, weight=1)
        watch_tab.rowconfigure(0, weight=1)
        ai_tab.columnconfigure(0, weight=1)
        ai_tab.rowconfigure(0, weight=1)
        alerts_tab.columnconfigure(0, weight=1)
        alerts_tab.rowconfigure(0, weight=1)

        self.notebook.add(feed_tab, text="Feed")
        self.notebook.add(watch_tab, text="Watchlist")
        self.notebook.add(ai_tab, text="AI Summary")
        self.notebook.add(alerts_tab, text="Alerts")
        self.notebook.select(feed_tab)

        self._build_results_panel(feed_tab)
        self._build_watch_panel(watch_tab)
        self._build_ai_summary_panel(ai_tab)
        self._build_alerts_panel(alerts_tab)

    def _build_watch_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        ttk.Label(panel, text="Watchlist", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text="Authors use structured arXiv author search. Institution tracking is keyword-based because arXiv does not expose structured affiliations.",
            style="Muted.TLabel",
            wraplength=310,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 12))

        form = ttk.Frame(panel, style="Panel.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)

        ttk.Label(form, text="Type").grid(row=0, column=0, sticky="w")
        self.kind_combo = ttk.Combobox(form, state="readonly", values=[kind.title() for kind in WATCH_TYPES])
        self.kind_combo.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.kind_combo.set("Author")

        ttk.Label(form, text="Label").grid(row=2, column=0, sticky="w")
        self.label_entry = ttk.Entry(form)
        self.label_entry.grid(row=3, column=0, sticky="ew", pady=(2, 8))

        ttk.Label(form, text="Search Text").grid(row=4, column=0, sticky="w")
        self.query_entry = ttk.Entry(form)
        self.query_entry.grid(row=5, column=0, sticky="ew", pady=(2, 8))

        ttk.Label(form, text="Notes").grid(row=6, column=0, sticky="w")
        self.notes_text = tk.Text(
            form,
            height=4,
            relief="solid",
            wrap="word",
            bd=1,
            background="#fffdf8",
            foreground="#1f1b16",
            insertbackground="#1f1b16",
        )
        self.notes_text.grid(row=7, column=0, sticky="ew", pady=(2, 10))

        button_bar = ttk.Frame(form, style="Panel.TFrame")
        button_bar.grid(row=8, column=0, sticky="ew")
        for column in range(3):
            button_bar.columnconfigure(column, weight=1)
        ttk.Button(button_bar, text="Add", style="Accent.TButton", command=self._add_watch_item).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(button_bar, text="Update", style="Secondary.TButton", command=self._update_watch_item).grid(
            row=0, column=1, sticky="ew", padx=3
        )
        ttk.Button(button_bar, text="Delete", command=self._delete_watch_item).grid(
            row=0, column=2, sticky="ew", padx=(6, 0)
        )

        ttk.Label(panel, text="Saved Targets", style="Section.TLabel").grid(row=3, column=0, sticky="w", pady=(16, 8))
        self.watch_tree = ttk.Treeview(panel, columns=("kind", "query"), show="headings", height=14)
        self.watch_tree.heading("kind", text="Type")
        self.watch_tree.heading("query", text="Label / Search")
        self.watch_tree.column("kind", width=90, anchor="center")
        self.watch_tree.column("query", width=220, anchor="w")
        self.watch_tree.grid(row=4, column=0, sticky="nsew")
        self.watch_tree.bind("<<TreeviewSelect>>", self._on_watch_selected)

        watch_scroll = ttk.Scrollbar(panel, orient="vertical", command=self.watch_tree.yview)
        self.watch_tree.configure(yscrollcommand=watch_scroll.set)
        watch_scroll.grid(row=4, column=1, sticky="ns")

        ttk.Button(panel, text="Clear Form", command=self._clear_form).grid(row=5, column=0, sticky="e", pady=(10, 0))

    def _build_results_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        panel.rowconfigure(4, weight=1)

        controls = ttk.Frame(panel, style="Panel.TFrame")
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(7, weight=1)

        ttk.Label(controls, text="Results", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))

        ttk.Label(controls, text="Mode").grid(row=0, column=1, sticky="w")
        mode_combo = ttk.Combobox(controls, state="readonly", textvariable=self.query_mode, values=("latest", "daily"))
        mode_combo.grid(row=0, column=2, sticky="w", padx=(4, 12))

        ttk.Label(controls, text="Per Watch").grid(row=0, column=3, sticky="w")
        limit_combo = ttk.Combobox(controls, width=6, textvariable=self.limit_var, values=("10", "20", "30", "50"))
        limit_combo.grid(row=0, column=4, sticky="w", padx=(4, 12))

        ttk.Label(controls, text="Daily Window").grid(row=0, column=5, sticky="w")
        days_combo = ttk.Combobox(controls, width=6, textvariable=self.daily_days_var, values=("1", "2", "3", "7"))
        days_combo.grid(row=0, column=6, sticky="w", padx=(4, 12))

        ttk.Button(controls, text="Fetch Matches", style="Accent.TButton", command=self._run_fetch).grid(
            row=0, column=7, sticky="e"
        )

        ttk.Label(panel, textvariable=self.selection_count_text, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 10)
        )

        self.results_tree = ttk.Treeview(
            panel,
            columns=("published", "title", "matches", "authors"),
            show="headings",
        )
        self.results_tree.heading("published", text="Published")
        self.results_tree.heading("title", text="Paper")
        self.results_tree.heading("matches", text="Matched Watches")
        self.results_tree.heading("authors", text="Authors")
        self.results_tree.column("published", width=110, anchor="center")
        self.results_tree.column("title", width=420, anchor="w")
        self.results_tree.column("matches", width=220, anchor="w")
        self.results_tree.column("authors", width=240, anchor="w")
        self.results_tree.grid(row=2, column=0, sticky="nsew")
        self.results_tree.bind("<<TreeviewSelect>>", self._on_result_selected)

        result_scroll = ttk.Scrollbar(panel, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=result_scroll.set)
        result_scroll.grid(row=2, column=1, sticky="ns")

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 8))
        ttk.Button(actions, text="Open Abstract", command=self._open_abstract).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(actions, text="Open PDF", command=self._open_pdf).grid(row=0, column=1, sticky="w")

        detail_card = ttk.Frame(panel, style="Panel.TFrame")
        detail_card.grid(row=4, column=0, sticky="nsew")
        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(1, weight=1)

        ttk.Label(detail_card, text="Paper Details", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.details_text = tk.Text(
            detail_card,
            relief="solid",
            wrap="word",
            bd=1,
            padx=12,
            pady=12,
            background="#fffdf8",
            foreground="#1f1b16",
            insertbackground="#1f1b16",
        )
        self.details_text.grid(row=1, column=0, sticky="nsew")
        self.details_text.configure(state="disabled")

    def _build_ai_summary_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        ttk.Label(panel, text="AI Summary", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text=(
                "Generate a fresh daily watchlist digest with OpenAI or Anthropic. "
                "Your API key is stored locally in arxiv_secretary.db as plain text."
            ),
            style="Muted.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 14))

        settings_card = ttk.Frame(panel, style="Panel.TFrame")
        settings_card.grid(row=2, column=0, sticky="ew")
        settings_card.columnconfigure(1, weight=1)

        ttk.Label(settings_card, text="Provider").grid(row=0, column=0, sticky="w")
        self.ai_provider_combo = ttk.Combobox(
            settings_card,
            state="readonly",
            textvariable=self.ai_provider_var,
            values=("openai", "anthropic"),
            width=16,
        )
        self.ai_provider_combo.grid(row=0, column=1, sticky="w", pady=(0, 10))

        ttk.Label(settings_card, text="Model").grid(row=1, column=0, sticky="w")
        self.ai_model_entry = ttk.Entry(settings_card, textvariable=self.ai_model_var)
        self.ai_model_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(settings_card, text="API Key").grid(row=2, column=0, sticky="w")
        self.ai_key_entry = ttk.Entry(settings_card, textvariable=self.ai_key_var, show="*")
        self.ai_key_entry.grid(row=2, column=1, sticky="ew", pady=(0, 10))

        button_bar = ttk.Frame(settings_card, style="Panel.TFrame")
        button_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        ttk.Button(button_bar, text="Save Settings", command=self._save_ai_settings).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Button(button_bar, text="Generate Daily Summary", style="Accent.TButton", command=self._run_ai_summary).grid(
            row=0, column=1, sticky="w"
        )

        ttk.Label(panel, textvariable=self.ai_summary_status_text, style="Muted.TLabel").grid(
            row=3, column=0, sticky="nw", pady=(14, 10)
        )

        summary_card = ttk.Frame(panel, style="Panel.TFrame")
        summary_card.grid(row=4, column=0, sticky="nsew")
        summary_card.columnconfigure(0, weight=1)
        summary_card.rowconfigure(1, weight=1)

        ttk.Label(summary_card, text="Daily Summary Output", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.ai_summary_text = tk.Text(
            summary_card,
            relief="solid",
            wrap="word",
            bd=1,
            padx=12,
            pady=12,
            background="#fffdf8",
            foreground="#1f1b16",
            insertbackground="#1f1b16",
        )
        self.ai_summary_text.grid(row=1, column=0, sticky="nsew")
        self._configure_ai_summary_tags()
        self.ai_summary_text.configure(state="disabled")
        self._set_ai_summary_text("No AI summary yet. Generate one from this tab to see a daily digest.")

    def _build_alerts_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        ttk.Label(panel, text="Alerts", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text=(
                "Scheduled alerts run while this app is open. You can trigger a daily, weekly, or monthly fetch, "
                "show a desktop pop-up, and optionally send an email through your own SMTP account."
            ),
            style="Muted.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 14))

        settings_card = ttk.Frame(panel, style="Panel.TFrame")
        settings_card.grid(row=2, column=0, sticky="nsew")
        settings_card.columnconfigure(1, weight=1)
        settings_card.columnconfigure(3, weight=1)

        ttk.Checkbutton(settings_card, text="Enable scheduled alerts", variable=self.alert_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(settings_card, text="Desktop pop-up", variable=self.alert_desktop_var).grid(
            row=0, column=2, sticky="w", padx=(12, 0)
        )
        ttk.Checkbutton(settings_card, text="Email notification", variable=self.alert_email_var).grid(
            row=0, column=3, sticky="w"
        )

        ttk.Label(settings_card, text="Frequency").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.alert_frequency_combo = ttk.Combobox(
            settings_card,
            state="readonly",
            textvariable=self.alert_frequency_var,
            values=FREQUENCIES,
            width=16,
        )
        self.alert_frequency_combo.grid(row=1, column=1, sticky="w", pady=(12, 0))

        ttk.Label(settings_card, text="Hour").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(12, 0))
        self.alert_hour_combo = ttk.Combobox(
            settings_card,
            textvariable=self.alert_hour_var,
            values=[f"{value:02d}" for value in range(24)],
            width=6,
        )
        self.alert_hour_combo.grid(row=1, column=3, sticky="w", pady=(12, 0))

        ttk.Label(settings_card, text="Minute").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.alert_minute_combo = ttk.Combobox(
            settings_card,
            textvariable=self.alert_minute_var,
            values=[f"{value:02d}" for value in range(0, 60, 5)],
            width=6,
        )
        self.alert_minute_combo.grid(row=2, column=1, sticky="w", pady=(10, 0))

        self.alert_weekday_label = ttk.Label(settings_card, text="Weekday")
        self.alert_weekday_label.grid(row=2, column=2, sticky="w", padx=(12, 0), pady=(10, 0))
        self.alert_weekday_combo = ttk.Combobox(
            settings_card,
            state="readonly",
            textvariable=self.alert_weekday_var,
            values=WEEKDAYS,
            width=16,
        )
        self.alert_weekday_combo.grid(row=2, column=3, sticky="w", pady=(10, 0))

        self.alert_month_day_label = ttk.Label(settings_card, text="Month Day")
        self.alert_month_day_label.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.alert_month_day_combo = ttk.Combobox(
            settings_card,
            textvariable=self.alert_month_day_var,
            values=[str(value) for value in range(1, 32)],
            width=6,
        )
        self.alert_month_day_combo.grid(row=3, column=1, sticky="w", pady=(10, 0))

        ttk.Label(settings_card, text="SMTP Host").grid(row=4, column=0, sticky="w", pady=(16, 0))
        self.smtp_host_entry = ttk.Entry(settings_card, textvariable=self.smtp_host_var)
        self.smtp_host_entry.grid(row=4, column=1, sticky="ew", pady=(16, 0))

        ttk.Label(settings_card, text="SMTP Port").grid(row=4, column=2, sticky="w", padx=(12, 0), pady=(16, 0))
        self.smtp_port_entry = ttk.Entry(settings_card, textvariable=self.smtp_port_var)
        self.smtp_port_entry.grid(row=4, column=3, sticky="ew", pady=(16, 0))

        ttk.Label(settings_card, text="SMTP Username").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.smtp_username_entry = ttk.Entry(settings_card, textvariable=self.smtp_username_var)
        self.smtp_username_entry.grid(row=5, column=1, sticky="ew", pady=(10, 0))

        ttk.Label(settings_card, text="SMTP Password").grid(row=5, column=2, sticky="w", padx=(12, 0), pady=(10, 0))
        self.smtp_password_entry = ttk.Entry(settings_card, textvariable=self.smtp_password_var, show="*")
        self.smtp_password_entry.grid(row=5, column=3, sticky="ew", pady=(10, 0))

        self.smtp_tls_check = ttk.Checkbutton(settings_card, text="Use TLS", variable=self.smtp_tls_var)
        self.smtp_tls_check.grid(row=6, column=0, sticky="w", pady=(10, 0))

        ttk.Label(settings_card, text="From Email").grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.email_from_entry = ttk.Entry(settings_card, textvariable=self.email_from_var)
        self.email_from_entry.grid(row=7, column=1, sticky="ew", pady=(10, 0))

        ttk.Label(settings_card, text="To Email").grid(row=7, column=2, sticky="w", padx=(12, 0), pady=(10, 0))
        self.email_to_entry = ttk.Entry(settings_card, textvariable=self.email_to_var)
        self.email_to_entry.grid(row=7, column=3, sticky="ew", pady=(10, 0))

        button_bar = ttk.Frame(settings_card, style="Panel.TFrame")
        button_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        ttk.Button(button_bar, text="Save Alert Settings", command=self._save_alert_settings).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Button(button_bar, text="Run Alert Now", style="Accent.TButton", command=self._run_alert_now).grid(
            row=0, column=1, sticky="w"
        )

        footer = ttk.Frame(panel, style="Panel.TFrame")
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 10))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.alert_status_text, style="Muted.TLabel", wraplength=980, justify="left").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(footer, textvariable=self.alert_next_run_text, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _configure_ai_summary_tags(self) -> None:
        self.ai_summary_text.tag_configure("md_body", spacing1=2, spacing3=2)
        self.ai_summary_text.tag_configure("md_h1", font=("Georgia", 15, "bold"), foreground="#1b4332", spacing1=8, spacing3=6)
        self.ai_summary_text.tag_configure("md_h2", font=("Georgia", 13, "bold"), foreground="#7a3b10", spacing1=8, spacing3=4)
        self.ai_summary_text.tag_configure("md_h3", font=("Georgia", 12, "bold"), foreground="#7a3b10", spacing1=6, spacing3=3)
        self.ai_summary_text.tag_configure("md_bold", font=("Segoe UI", 10, "bold"))
        self.ai_summary_text.tag_configure("md_bullet", lmargin1=16, lmargin2=32, spacing1=2, spacing3=2)
        self.ai_summary_text.tag_configure("md_rule", foreground="#9a8a72", justify="center", spacing1=6, spacing3=6)

    def _restore_defaults(self) -> None:
        self.kind_combo.set("Author")

    def _load_ai_settings(self) -> None:
        provider = self.ai_provider_var.get().strip().lower() or "openai"
        if provider not in DEFAULT_MODELS:
            provider = "openai"
            self.ai_provider_var.set(provider)
        self.ai_model_var.set(self.storage.get_setting(f"ai_{provider}_model", DEFAULT_MODELS[provider]))
        self.ai_key_var.set(self.storage.get_setting(f"ai_{provider}_api_key", ""))

    def _load_alert_settings(self) -> None:
        self.alert_enabled_var.set(self.storage.get_setting("alert_enabled", "1") == "1")
        self.alert_frequency_var.set(self.storage.get_setting("alert_frequency", "daily"))
        self.alert_hour_var.set(self.storage.get_setting("alert_hour", "12"))
        self.alert_minute_var.set(self.storage.get_setting("alert_minute", "00"))
        weekday_index = int(self.storage.get_setting("alert_weekday", "0") or "0")
        weekday_index = min(max(weekday_index, 0), len(WEEKDAYS) - 1)
        self.alert_weekday_var.set(WEEKDAYS[weekday_index])
        self.alert_month_day_var.set(self.storage.get_setting("alert_month_day", "1"))
        self.alert_desktop_var.set(self.storage.get_setting("alert_desktop_enabled", "1") == "1")
        self.alert_email_var.set(self.storage.get_setting("alert_email_enabled", "0") == "1")
        self.smtp_host_var.set(self.storage.get_setting("alert_smtp_host", ""))
        self.smtp_port_var.set(self.storage.get_setting("alert_smtp_port", "587"))
        self.smtp_username_var.set(self.storage.get_setting("alert_smtp_username", ""))
        self.smtp_password_var.set(self.storage.get_setting("alert_smtp_password", ""))
        self.smtp_tls_var.set(self.storage.get_setting("alert_smtp_use_tls", "1") == "1")
        self.email_from_var.set(self.storage.get_setting("alert_email_from", ""))
        self.email_to_var.set(self.storage.get_setting("alert_email_to", ""))
        self._sync_alert_field_states()
        self._update_alert_preview()

    def _save_ai_settings(self) -> None:
        provider = self.ai_provider_var.get().strip().lower()
        if provider not in DEFAULT_MODELS:
            messagebox.showerror("Invalid provider", "Choose either openai or anthropic.")
            return
        self.storage.set_setting("ai_provider", provider)
        self.storage.set_setting(f"ai_{provider}_model", self.ai_model_var.get().strip())
        self.storage.set_setting(f"ai_{provider}_api_key", self.ai_key_var.get().strip())
        self.ai_summary_status_text.set(f"Saved {provider.title()} settings locally.")

    def _on_ai_provider_changed(self, *_args: object) -> None:
        self._load_ai_settings()

    def _current_alert_settings(self) -> AlertSettings:
        weekday_label = self.alert_weekday_var.get().strip()
        weekday_index = WEEKDAYS.index(weekday_label) if weekday_label in WEEKDAYS else 0
        settings = AlertSettings(
            enabled=self.alert_enabled_var.get(),
            frequency=self.alert_frequency_var.get().strip().lower(),
            hour=int(self.alert_hour_var.get() or "12"),
            minute=int(self.alert_minute_var.get() or "0"),
            weekday=weekday_index,
            month_day=int(self.alert_month_day_var.get() or "1"),
            desktop_enabled=self.alert_desktop_var.get(),
            email_enabled=self.alert_email_var.get(),
            smtp_host=self.smtp_host_var.get(),
            smtp_port=int(self.smtp_port_var.get() or "587"),
            smtp_username=self.smtp_username_var.get(),
            smtp_password=self.smtp_password_var.get(),
            smtp_use_tls=self.smtp_tls_var.get(),
            email_from=self.email_from_var.get(),
            email_to=self.email_to_var.get(),
        )
        return normalize_alert_settings(settings)

    def _save_alert_settings(self) -> None:
        try:
            settings = self._current_alert_settings()
        except ValueError:
            messagebox.showerror("Invalid alert settings", "Hour, minute, month day, and SMTP port must be valid numbers.")
            return

        self.storage.set_setting("alert_enabled", "1" if settings.enabled else "0")
        self.storage.set_setting("alert_frequency", settings.frequency)
        self.storage.set_setting("alert_hour", str(settings.hour))
        self.storage.set_setting("alert_minute", f"{settings.minute:02d}")
        self.storage.set_setting("alert_weekday", str(settings.weekday))
        self.storage.set_setting("alert_month_day", str(settings.month_day))
        self.storage.set_setting("alert_desktop_enabled", "1" if settings.desktop_enabled else "0")
        self.storage.set_setting("alert_email_enabled", "1" if settings.email_enabled else "0")
        self.storage.set_setting("alert_smtp_host", settings.smtp_host)
        self.storage.set_setting("alert_smtp_port", str(settings.smtp_port))
        self.storage.set_setting("alert_smtp_username", settings.smtp_username)
        self.storage.set_setting("alert_smtp_password", settings.smtp_password)
        self.storage.set_setting("alert_smtp_use_tls", "1" if settings.smtp_use_tls else "0")
        self.storage.set_setting("alert_email_from", settings.email_from)
        self.storage.set_setting("alert_email_to", settings.email_to)
        self.alert_status_text.set("Alert settings saved locally.")
        self._update_alert_preview()

    def _sync_alert_field_states(self) -> None:
        frequency = self.alert_frequency_var.get().strip().lower()
        weekly_state = "readonly" if frequency == "weekly" else "disabled"
        monthly_state = "normal" if frequency == "monthly" else "disabled"
        self.alert_weekday_combo.configure(state=weekly_state)
        self.alert_month_day_combo.configure(state=monthly_state)

        email_state = "normal" if self.alert_email_var.get() else "disabled"
        for widget in (
            self.smtp_host_entry,
            self.smtp_port_entry,
            self.smtp_username_entry,
            self.smtp_password_entry,
            self.email_from_entry,
            self.email_to_entry,
        ):
            widget.configure(state=email_state)
        self.smtp_tls_check.configure(state="normal" if self.alert_email_var.get() else "disabled")

    def _update_alert_preview(self) -> None:
        try:
            settings = self._current_alert_settings()
        except ValueError:
            self.alert_next_run_text.set("Next run unavailable until the alert fields contain valid numbers.")
            return
        if not settings.enabled:
            self.alert_next_run_text.set("Alerts are disabled.")
            return
        self.alert_next_run_text.set(describe_schedule(settings, datetime.now().astimezone()))

    def _on_alert_settings_changed(self, *_args: object) -> None:
        self._sync_alert_field_states()
        self._update_alert_preview()

    def _load_watch_items(self) -> None:
        self.watch_items = self.storage.list_watch_items()
        for row in self.watch_tree.get_children():
            self.watch_tree.delete(row)
        for item in self.watch_items:
            text = f"{item.label}  |  {item.query}"
            self.watch_tree.insert("", "end", iid=str(item.id), values=(item.kind.title(), text))

    def _add_watch_item(self) -> None:
        item = self._collect_form_item(existing_id=None)
        if item is None:
            return
        self.storage.add_watch_item(item)
        self._load_watch_items()
        self._clear_form()
        self.status_text.set(f"Added watch: {item.label}")

    def _update_watch_item(self) -> None:
        if self.selected_watch_id is None:
            messagebox.showinfo("Select a watch", "Choose a saved watch target to update.")
            return
        item = self._collect_form_item(existing_id=self.selected_watch_id)
        if item is None:
            return
        self.storage.update_watch_item(item)
        self._load_watch_items()
        self.status_text.set(f"Updated watch: {item.label}")

    def _delete_watch_item(self) -> None:
        if self.selected_watch_id is None:
            messagebox.showinfo("Select a watch", "Choose a saved watch target to delete.")
            return
        selected = self._find_watch_by_id(self.selected_watch_id)
        if selected is None:
            return
        if not messagebox.askyesno("Delete watch", f"Delete '{selected.label}' from your watchlist?"):
            return
        self.storage.delete_watch_item(self.selected_watch_id)
        self._load_watch_items()
        self._clear_form()
        self.status_text.set(f"Deleted watch: {selected.label}")

    def _collect_form_item(self, *, existing_id: int | None) -> WatchItem | None:
        kind = self.kind_combo.get().strip().lower()
        label = self.label_entry.get().strip()
        query = self.query_entry.get().strip()
        notes = self.notes_text.get("1.0", "end").strip()
        if kind not in WATCH_TYPES:
            messagebox.showerror("Invalid type", "Choose Author, Institution, or Topic.")
            return None
        if not label or not query:
            messagebox.showerror("Missing fields", "Label and search text are required.")
            return None
        return WatchItem(id=existing_id, kind=kind, label=label, query=query, notes=notes)

    def _on_watch_selected(self, _event: object) -> None:
        selection = self.watch_tree.selection()
        if not selection:
            return
        item_id = int(selection[0])
        item = self._find_watch_by_id(item_id)
        if item is None:
            return
        self.selected_watch_id = item.id
        self.kind_combo.set(item.kind.title())
        self.label_entry.delete(0, "end")
        self.label_entry.insert(0, item.label)
        self.query_entry.delete(0, "end")
        self.query_entry.insert(0, item.query)
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", item.notes)

    def _clear_form(self) -> None:
        self.selected_watch_id = None
        self.kind_combo.set("Author")
        self.label_entry.delete(0, "end")
        self.query_entry.delete(0, "end")
        self.notes_text.delete("1.0", "end")
        for selected in self.watch_tree.selection():
            self.watch_tree.selection_remove(selected)

    def _find_watch_by_id(self, item_id: int) -> WatchItem | None:
        for item in self.watch_items:
            if item.id == item_id:
                return item
        return None

    def _collect_feed_results(
        self,
        watch_items: list[WatchItem],
        *,
        mode: str,
        max_results: int,
        daily_days: int,
    ) -> tuple[list[Paper], list[str]]:
        merged: dict[str, Paper] = {}
        matched_dates: dict[str, datetime] = {}
        failures: list[str] = []
        for item in watch_items:
            try:
                papers = fetch_matches(
                    item,
                    max_results=max_results,
                    only_last_days=daily_days if mode == "daily" else None,
                )
            except Exception as exc:
                failures.append(f"{item.label}: {exc}")
                continue

            for paper in papers:
                existing = merged.get(paper.entry_id)
                if existing is None:
                    merged[paper.entry_id] = paper
                    matched_dates[paper.entry_id] = parse_arxiv_datetime(paper.published)
                else:
                    existing.matched_watch_labels.update(paper.matched_watch_labels)

        ordered = sorted(
            merged.values(),
            key=lambda paper: matched_dates.get(paper.entry_id, datetime.min.replace(tzinfo=UTC)),
            reverse=True,
        )
        return ordered, failures

    def _run_fetch(self) -> None:
        watch_items = self.storage.list_watch_items()
        if not watch_items:
            messagebox.showinfo("No watches", "Add at least one author, institution, or topic first.")
            return
        try:
            max_results = max(1, int(self.limit_var.get()))
            daily_days = max(1, int(self.daily_days_var.get()))
        except ValueError:
            messagebox.showerror("Invalid settings", "Per Watch and Daily Window must be whole numbers.")
            return

        self.storage.set_setting("max_results", str(max_results))
        self.storage.set_setting("daily_days", str(daily_days))

        mode = self.query_mode.get().strip().lower()
        if mode not in {"latest", "daily"}:
            messagebox.showerror("Invalid mode", "Choose either latest or daily.")
            return

        self.status_text.set("Fetching arXiv matches...")
        self.selection_count_text.set("Loading papers...")
        self.executor.submit(self._fetch_worker, watch_items, mode, max_results, daily_days)

    def _run_ai_summary(self) -> None:
        watch_items = self.storage.list_watch_items()
        if not watch_items:
            messagebox.showinfo("No watches", "Add at least one author, institution, or topic first.")
            return
        provider = self.ai_provider_var.get().strip().lower()
        if provider not in DEFAULT_MODELS:
            messagebox.showerror("Invalid provider", "Choose either openai or anthropic.")
            return

        api_key = self.ai_key_var.get().strip()
        model = self.ai_model_var.get().strip()
        if not api_key:
            messagebox.showerror("Missing API key", f"Enter a {provider.title()} API key first.")
            return
        if not model:
            messagebox.showerror("Missing model", "Enter a model name before generating the summary.")
            return

        try:
            max_results = max(1, int(self.limit_var.get()))
            daily_days = max(1, int(self.daily_days_var.get()))
        except ValueError:
            messagebox.showerror("Invalid settings", "Per Watch and Daily Window must be whole numbers.")
            return

        self.storage.set_setting("max_results", str(max_results))
        self.storage.set_setting("daily_days", str(daily_days))
        self._save_ai_settings()

        self.status_text.set("Generating AI daily summary...")
        self.ai_summary_status_text.set("Refreshing the daily feed and requesting an AI summary...")
        self._set_ai_summary_text("Generating daily summary...")
        self.executor.submit(self._ai_summary_worker, watch_items, provider, api_key, model, max_results, daily_days)

    def _run_alert_now(self) -> None:
        watch_items = self.storage.list_watch_items()
        if not watch_items:
            messagebox.showinfo("No watches", "Add at least one author, institution, or topic first.")
            return
        try:
            settings = self._current_alert_settings()
        except ValueError:
            messagebox.showerror("Invalid alert settings", "Hour, minute, month day, and SMTP port must be valid numbers.")
            return
        try:
            max_results = max(1, int(self.limit_var.get()))
            daily_days = max(1, int(self.daily_days_var.get()))
        except ValueError:
            messagebox.showerror("Invalid feed settings", "Per Watch and Daily Window must be whole numbers.")
            return
        self._save_alert_settings()
        self.status_text.set("Running alert fetch...")
        self.alert_status_text.set("Running the alert manually...")
        self.executor.submit(self._scheduled_alert_worker, settings, watch_items, max_results, daily_days, "manual")

    def _fetch_worker(self, watch_items: list[WatchItem], mode: str, max_results: int, daily_days: int) -> None:
        try:
            ordered, failures = self._collect_feed_results(
                watch_items,
                mode=mode,
                max_results=max_results,
                daily_days=daily_days,
            )
            self.queue.put(("fetch_success", {"mode": mode, "papers": ordered, "failures": failures}))
        except Exception as exc:
            self.queue.put(("fetch_error", exc))

    def _ai_summary_worker(
        self,
        watch_items: list[WatchItem],
        provider: str,
        api_key: str,
        model: str,
        max_results: int,
        daily_days: int,
    ) -> None:
        try:
            papers, failures = self._collect_feed_results(
                watch_items,
                mode="daily",
                max_results=max_results,
                daily_days=daily_days,
            )
            summary_mode = "daily"
            if not papers:
                papers, latest_failures = self._collect_feed_results(
                    watch_items,
                    mode="latest",
                    max_results=max_results,
                    daily_days=daily_days,
                )
                failures.extend(latest_failures)
                summary_mode = "latest"
            summary = generate_daily_summary(
                provider=provider,
                api_key=api_key,
                model=model,
                papers=papers,
                daily_days=daily_days,
            )
            self.queue.put(
                (
                    "ai_summary_success",
                    {
                        "provider": provider,
                        "model": model,
                        "papers": papers,
                        "failures": failures,
                        "summary_mode": summary_mode,
                        "summary": summary,
                    },
                )
            )
        except Exception as exc:
            self.queue.put(("ai_summary_error", exc))

    def _scheduled_alert_worker(
        self,
        settings: AlertSettings,
        watch_items: list[WatchItem],
        max_results: int,
        daily_days: int,
        source: str,
    ) -> None:
        try:
            papers, failures = self._collect_feed_results(
                watch_items,
                mode="daily",
                max_results=max_results,
                daily_days=daily_days,
            )
            message = build_notification_message(papers, mode="daily")
            email_status = ""
            if settings.email_enabled:
                subject = f"arXiv Secretary Alert: {len(papers)} daily match(es)"
                try:
                    send_email_notification(settings, subject=subject, body=message)
                    email_status = " Email sent."
                except Exception as exc:
                    email_status = f" Email failed: {exc}"
            self.queue.put(
                (
                    "scheduled_alert_success",
                    {
                        "source": source,
                        "papers": papers,
                        "failures": failures,
                        "message": message,
                        "settings": settings,
                        "email_status": email_status,
                    },
                )
            )
        except Exception as exc:
            self.queue.put(("scheduled_alert_error", {"source": source, "error": exc}))

    def _poll_queue(self) -> None:
        try:
            while True:
                message, payload = self.queue.get_nowait()
                if message == "fetch_success":
                    self._apply_results(payload)
                elif message == "ai_summary_success":
                    self._apply_ai_summary(payload)
                elif message == "scheduled_alert_success":
                    self._apply_scheduled_alert(payload)
                elif message == "fetch_error":
                    self.status_text.set("Fetch failed")
                    messagebox.showerror("Fetch failed", str(payload))
                elif message == "ai_summary_error":
                    self.status_text.set("AI summary failed")
                    self.ai_summary_status_text.set("The AI summary request failed.")
                    messagebox.showerror("AI summary failed", str(payload))
                elif message == "scheduled_alert_error":
                    data = payload if isinstance(payload, dict) else {}
                    error = data.get("error", payload)
                    self.status_text.set("Alert run failed")
                    self.alert_status_text.set(f"Alert run failed: {error}")
                    messagebox.showerror("Alert run failed", str(error))
        except Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _poll_alert_schedule(self) -> None:
        try:
            settings = self._current_alert_settings()
            self._update_alert_preview()
            should_run, marker = should_run_now(settings, datetime.now().astimezone(), self.last_alert_marker)
            if should_run:
                watch_items = self.storage.list_watch_items()
                if watch_items:
                    max_results = max(1, int(self.limit_var.get()))
                    daily_days = max(1, int(self.daily_days_var.get()))
                    self.last_alert_marker = marker
                    self.storage.set_setting("alert_last_run_marker", marker)
                    self.status_text.set("Running scheduled alert...")
                    self.alert_status_text.set("Scheduled alert is running now.")
                    self.executor.submit(
                        self._scheduled_alert_worker,
                        settings,
                        watch_items,
                        max_results,
                        daily_days,
                        "scheduled",
                    )
        except Exception:
            pass
        self.root.after(30000, self._poll_alert_schedule)

    def _apply_results(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        papers = data.get("papers", []) if isinstance(data, dict) else []
        failures = data.get("failures", []) if isinstance(data, dict) else []
        mode = data.get("mode", "latest") if isinstance(data, dict) else "latest"
        self.results = list(papers)
        self.result_index = {paper.entry_id: paper for paper in self.results}

        for row in self.results_tree.get_children():
            self.results_tree.delete(row)

        for paper in self.results:
            published = self._format_date(paper.published)
            matches = ", ".join(sorted(paper.matched_watch_labels))
            self.results_tree.insert(
                "",
                "end",
                iid=paper.entry_id,
                values=(published, paper.title, matches, paper.short_authors),
            )

        if self.results:
            label = "daily digest" if mode == "daily" else "latest matches"
            self.selection_count_text.set(f"{len(self.results)} papers loaded for your {label}.")
            self.status_text.set(f"Loaded {len(self.results)} papers")
            first_item = self.results[0]
            self.results_tree.selection_set(first_item.entry_id)
            self.results_tree.focus(first_item.entry_id)
            self._show_paper_details(first_item)
        else:
            self.selection_count_text.set("No papers matched your current watchlist settings.")
            self.status_text.set("No matches found")
            self._set_details_text("No papers matched this query run.")

        if mode == "daily":
            self.last_daily_results = list(self.results)

        if failures:
            preview = "\n".join(failures[:5])
            if len(failures) > 5:
                preview += f"\n...and {len(failures) - 5} more."
            messagebox.showwarning("Some watches failed", preview)

    def _apply_ai_summary(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        papers = data.get("papers", []) if isinstance(data, dict) else []
        failures = data.get("failures", []) if isinstance(data, dict) else []
        summary = data.get("summary", "") if isinstance(data, dict) else ""
        provider = data.get("provider", "") if isinstance(data, dict) else ""
        model = data.get("model", "") if isinstance(data, dict) else ""
        summary_mode = data.get("summary_mode", "daily") if isinstance(data, dict) else "daily"

        self._apply_results({"mode": summary_mode, "papers": papers, "failures": failures})
        self.last_daily_summary_text = summary
        self._set_ai_summary_text(summary)
        provider_label = provider.title() if isinstance(provider, str) else "AI"
        if summary_mode == "daily":
            self.ai_summary_status_text.set(f"Daily summary generated with {provider_label} ({model}).")
        else:
            self.ai_summary_status_text.set(
                f"No papers were found in the daily window, so the summary used latest matches with {provider_label} ({model})."
            )
        self.status_text.set("AI daily summary ready")

    def _apply_scheduled_alert(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        papers = data.get("papers", []) if isinstance(data, dict) else []
        failures = data.get("failures", []) if isinstance(data, dict) else []
        message = data.get("message", "") if isinstance(data, dict) else ""
        settings = data.get("settings") if isinstance(data, dict) else None
        source = data.get("source", "scheduled") if isinstance(data, dict) else "scheduled"
        email_status = data.get("email_status", "") if isinstance(data, dict) else ""

        self._apply_results({"mode": "daily", "papers": papers, "failures": failures})
        run_label = "Scheduled alert" if source == "scheduled" else "Manual alert"
        self.alert_status_text.set(f"{run_label} finished. {message}{email_status}")
        self.status_text.set(f"{run_label} ready")

        if isinstance(settings, AlertSettings) and settings.desktop_enabled:
            messagebox.showinfo(run_label, message)

    def _on_result_selected(self, _event: object) -> None:
        selection = self.results_tree.selection()
        if not selection:
            return
        paper = self.result_index.get(selection[0])
        if paper is not None:
            self._show_paper_details(paper)

    def _show_paper_details(self, paper: Paper) -> None:
        details = [
            paper.title,
            "",
            f"Published: {self._format_datetime(paper.published)}",
            f"Updated:   {self._format_datetime(paper.updated)}",
            f"Authors:   {', '.join(paper.authors) or 'Unknown'}",
            f"Primary:   {paper.primary_category or 'Unspecified'}",
            f"All tags:  {', '.join(paper.categories) or 'None'}",
            f"Matches:   {', '.join(sorted(paper.matched_watch_labels)) or 'None'}",
        ]
        if paper.comment:
            details.extend(["", f"Comment: {paper.comment}"])
        details.extend(
            [
                "",
                "Abstract",
                paper.summary or "No abstract available.",
                "",
                f"Abstract URL: {paper.abstract_url}",
                f"PDF URL:      {paper.pdf_url or 'Unavailable'}",
            ]
        )
        self._set_details_text("\n".join(details))

    def _set_details_text(self, content: str) -> None:
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", content)
        self.details_text.configure(state="disabled")

    def _set_ai_summary_text(self, content: str) -> None:
        self.ai_summary_text.configure(state="normal")
        self.ai_summary_text.delete("1.0", "end")
        self._render_markdown_like(self.ai_summary_text, content)
        self.ai_summary_text.configure(state="disabled")

    def _render_markdown_like(self, widget: tk.Text, content: str) -> None:
        lines = content.splitlines()
        if not lines:
            widget.insert("end", "")
            return

        for line in lines:
            stripped = line.strip()
            if not stripped:
                widget.insert("end", "\n")
                continue
            if stripped in {"---", "***"}:
                widget.insert("end", "────────────────────────────────────────\n", ("md_rule",))
                continue
            if stripped.startswith("### "):
                self._insert_markdown_inline(widget, stripped[4:].strip(), block_tag="md_h3")
                widget.insert("end", "\n")
                continue
            if stripped.startswith("## "):
                self._insert_markdown_inline(widget, stripped[3:].strip(), block_tag="md_h2")
                widget.insert("end", "\n")
                continue
            if stripped.startswith("# "):
                self._insert_markdown_inline(widget, stripped[2:].strip(), block_tag="md_h1")
                widget.insert("end", "\n")
                continue
            if stripped.startswith(("- ", "* ")):
                widget.insert("end", "• ", ("md_bullet",))
                self._insert_markdown_inline(widget, stripped[2:].strip(), block_tag="md_bullet")
                widget.insert("end", "\n")
                continue

            self._insert_markdown_inline(widget, stripped, block_tag="md_body")
            widget.insert("end", "\n")

    def _insert_markdown_inline(self, widget: tk.Text, text: str, *, block_tag: str) -> None:
        parts = re.split(r"(\*\*.*?\*\*)", text)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) >= 4:
                widget.insert("end", part[2:-2], (block_tag, "md_bold"))
            else:
                widget.insert("end", part, (block_tag,))

    def _open_abstract(self) -> None:
        paper = self._selected_paper()
        if paper is not None and paper.abstract_url:
            webbrowser.open(paper.abstract_url)

    def _open_pdf(self) -> None:
        paper = self._selected_paper()
        if paper is None:
            return
        if not paper.pdf_url:
            messagebox.showinfo("No PDF link", "This arXiv entry did not expose a PDF URL.")
            return
        webbrowser.open(paper.pdf_url)

    def _selected_paper(self) -> Paper | None:
        selection = self.results_tree.selection()
        if not selection:
            messagebox.showinfo("Select a paper", "Choose a paper from the results list first.")
            return None
        return self.result_index.get(selection[0])

    def _format_date(self, value: str) -> str:
        try:
            return parse_arxiv_datetime(value).strftime("%Y-%m-%d")
        except Exception:
            return value

    def _format_datetime(self, value: str) -> str:
        try:
            return parse_arxiv_datetime(value).astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value

    def _on_close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    ArxivSecretaryApp(root)
    root.mainloop()
