from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
import re
import sys
from time import sleep
import traceback
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
from .ai_summary import DEFAULT_MODELS, PROVIDER_LABELS, generate_daily_summary
from .models import Paper, WATCH_TYPES, WatchItem
from .paths import app_data_dir, database_path
from .storage import Storage


class ArxivSecretaryApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("arXiv Secretary")
        self.root.geometry("1120x740")
        self.root.minsize(760, 620)
        self._icon_image: tk.PhotoImage | None = None
        self.button_icons: dict[str, tk.PhotoImage] = {}

        self.storage = Storage(database_path())
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.queue: Queue[tuple[str, object]] = Queue()

        self.watch_items: list[WatchItem] = []
        self.results: list[Paper] = []
        self.result_index: dict[str, Paper] = {}
        self.selected_watch_id: int | None = None
        self.theme_mode = self.storage.get_setting("theme_mode", "system")
        if self.theme_mode not in {"system", "light", "dark"}:
            self.theme_mode = "system"
        self.dark_mode = False
        self.fetch_request_id = 0
        self.results_sort_column = "published"
        self.results_sort_reverse = True
        self.status_text = tk.StringVar(value="Ready")
        self.query_mode = tk.StringVar(value="Latest")
        self.limit_var = tk.StringVar(value=self.storage.get_setting("max_results", "20"))
        self.daily_days_var = tk.StringVar(value=self.storage.get_setting("daily_days", "1"))
        self.selection_count_text = tk.StringVar(value="No papers loaded yet.")
        self.last_daily_results: list[Paper] = []
        self.ai_provider_var = tk.StringVar(value=self._provider_label(self.storage.get_setting("ai_provider", "openai")))
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
        self.button_icons = self._create_button_icons()
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
        dark_mode = self._theme_is_dark()
        self.dark_mode = dark_mode
        if dark_mode:
            self.colors = {
                "app_bg": "#202020",
                "panel_bg": "#2b2b2b",
                "field_bg": "#1f1f1f",
                "text": "#f3f3f3",
                "muted": "#c7c7c7",
                "heading": "#8fd6c4",
                "section": "#f0c36b",
                "border": "#4a4a4a",
                "accent": "#0078d4",
                "accent_active": "#106ebe",
                "secondary": "#3a3a3a",
                "secondary_active": "#484848",
                "secondary_text": "#f3f3f3",
                "tree_heading_bg": "#3a3a3a",
                "tree_heading_text": "#f3f3f3",
                "selected_bg": "#264f78",
                "selected_fg": "#ffffff",
                "disabled": "#8a8a8a",
                "placeholder": "#8f8f8f",
            }
        else:
            self.colors = {
                "app_bg": "#f5efe3",
                "panel_bg": "#fbf7ee",
                "field_bg": "#fffdf8",
                "text": "#1f1b16",
                "muted": "#73685a",
                "heading": "#1b4332",
                "section": "#7a3b10",
                "border": "#d9cfbf",
                "accent": "#1b6b5b",
                "accent_active": "#145244",
                "secondary": "#dfb96b",
                "secondary_active": "#ccab5c",
                "secondary_text": "#2d2418",
                "tree_heading_bg": "#e9dbc5",
                "tree_heading_text": "#45392d",
                "selected_bg": "#cde4d6",
                "selected_fg": "#17372d",
                "disabled": "#9a8a72",
                "placeholder": "#8a7c69",
            }

        colors = self.colors
        self.root.configure(bg=colors["app_bg"])
        self.root.option_add("*Font", "{Segoe UI} 10")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=colors["app_bg"], foreground=colors["text"])
        style.configure("TFrame", background=colors["app_bg"])
        style.configure("Panel.TFrame", background=colors["panel_bg"])
        style.configure("TLabel", background=colors["panel_bg"], foreground=colors["text"])
        style.configure("Panel.TLabel", background=colors["panel_bg"], foreground=colors["text"])
        style.configure("Muted.TLabel", background=colors["app_bg"], foreground=colors["muted"])
        style.configure("PanelMuted.TLabel", background=colors["panel_bg"], foreground=colors["muted"])
        style.configure(
            "Header.TLabel",
            background=colors["app_bg"],
            foreground=colors["heading"],
            font=("{Segoe UI}", 18, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background=colors["panel_bg"],
            foreground=colors["section"],
            font=("{Segoe UI}", 11, "bold"),
        )
        style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground="#ffffff",
            borderwidth=0,
            focuscolor="none",
            padding=(9, 5),
        )
        style.map("Accent.TButton", background=[("active", colors["accent_active"]), ("disabled", colors["accent"])])
        style.configure(
            "Secondary.TButton",
            background=colors["secondary"],
            foreground=colors["secondary_text"],
            borderwidth=0,
            focuscolor="none",
            padding=(8, 4),
        )
        style.map("Secondary.TButton", background=[("active", colors["secondary_active"])])
        style.configure("TButton", padding=(8, 4), focuscolor="none")
        style.configure(
            "TEntry",
            fieldbackground=colors["field_bg"],
            foreground=colors["text"],
            insertcolor=colors["text"],
            padding=6,
        )
        style.configure(
            "TCombobox",
            fieldbackground=colors["field_bg"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", colors["field_bg"]),
                ("disabled", colors["panel_bg"]),
                ("!disabled", colors["field_bg"]),
            ],
            foreground=[("disabled", colors["disabled"]), ("readonly", colors["text"])],
            selectbackground=[("readonly", colors["field_bg"])],
            selectforeground=[("readonly", colors["text"])],
        )
        style.configure(
            "TCheckbutton",
            background=colors["panel_bg"],
            foreground=colors["text"],
            focuscolor="none",
        )
        style.map(
            "TCheckbutton",
            background=[("active", colors["panel_bg"]), ("disabled", colors["panel_bg"])],
            foreground=[("disabled", colors["disabled"])],
        )
        style.configure("TNotebook", background=colors["app_bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=colors["app_bg"],
            foreground=colors["text"],
            padding=(8, 4),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["panel_bg"]), ("active", colors["app_bg"])],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "Watch.Vertical.TScrollbar",
            background=colors["field_bg"],
            troughcolor=colors["panel_bg"],
            bordercolor=colors["panel_bg"],
            lightcolor=colors["field_bg"],
            darkcolor=colors["field_bg"],
            arrowcolor=colors["muted"],
            width=9,
        )
        style.map("Watch.Vertical.TScrollbar", background=[("active", colors["border"])])
        style.configure(
            "Treeview",
            background=colors["field_bg"],
            fieldbackground=colors["field_bg"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            background=colors["tree_heading_bg"],
            foreground=colors["tree_heading_text"],
            relief="flat",
            font=("{Segoe UI}", 10, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", colors["selected_bg"])],
            foreground=[("selected", colors["selected_fg"])],
        )

    def _theme_is_dark(self) -> bool:
        if self.theme_mode == "dark":
            return True
        if self.theme_mode == "light":
            return False
        return self._system_prefers_dark()

    def _system_prefers_dark(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
        except Exception:
            return False

    def _create_button_icons(self) -> dict[str, tk.PhotoImage]:
        text = self.colors["text"]
        muted = self.colors["muted"]
        accent = self.colors["accent"]
        pdf_red = "#d13438"

        abstract = tk.PhotoImage(width=16, height=16)
        self._draw_rectangle(abstract, 3, 2, 11, 13, text)
        self._draw_line(abstract, 11, 2, 13, 4, text)
        self._draw_line(abstract, 13, 4, 13, 13, text)
        self._draw_line(abstract, 6, 5, 10, 5, muted)
        self._draw_line(abstract, 6, 8, 11, 8, muted)
        self._draw_line(abstract, 6, 11, 10, 11, muted)
        self._draw_line(abstract, 9, 2, 13, 2, accent)
        self._draw_line(abstract, 13, 2, 13, 6, accent)
        self._draw_line(abstract, 10, 5, 13, 2, accent)

        pdf = tk.PhotoImage(width=16, height=16)
        self._draw_rectangle(pdf, 3, 2, 12, 14, text)
        self._draw_line(pdf, 11, 2, 13, 4, text)
        self._draw_line(pdf, 13, 4, 13, 14, text)
        self._fill_rectangle(pdf, 4, 8, 12, 12, pdf_red)
        for x, y in ((5, 9), (5, 10), (5, 11), (6, 9), (7, 9), (7, 10), (6, 11)):
            pdf.put("#ffffff", (x, y))
        for x, y in ((8, 9), (8, 10), (8, 11), (9, 9), (10, 10), (9, 11)):
            pdf.put("#ffffff", (x, y))
        for x, y in ((11, 9), (11, 10), (11, 11), (12, 9), (12, 10)):
            if x < 13:
                pdf.put("#ffffff", (x, y))

        return {"abstract": abstract, "pdf": pdf}

    def _draw_rectangle(self, image: tk.PhotoImage, left: int, top: int, right: int, bottom: int, color: str) -> None:
        self._draw_line(image, left, top, right, top, color)
        self._draw_line(image, right, top, right, bottom, color)
        self._draw_line(image, right, bottom, left, bottom, color)
        self._draw_line(image, left, bottom, left, top, color)

    def _fill_rectangle(self, image: tk.PhotoImage, left: int, top: int, right: int, bottom: int, color: str) -> None:
        for y in range(top, bottom + 1):
            self._draw_line(image, left, y, right, y, color)

    def _draw_line(self, image: tk.PhotoImage, x1: int, y1: int, x2: int, y2: int, color: str) -> None:
        dx = abs(x2 - x1)
        dy = -abs(y2 - y1)
        step_x = 1 if x1 < x2 else -1
        step_y = 1 if y1 < y2 else -1
        error = dx + dy
        x = x1
        y = y1
        while True:
            image.put(color, (x, y))
            if x == x2 and y == y2:
                break
            error2 = 2 * error
            if error2 >= dy:
                error += dy
                x += step_x
            if error2 <= dx:
                error += dx
                y += step_y

    def _toggle_theme(self) -> None:
        self.theme_mode = "light" if self.dark_mode else "dark"
        self.storage.set_setting("theme_mode", self.theme_mode)
        self._configure_style()
        self.button_icons = self._create_button_icons()
        self._refresh_theme_widgets()
        self._update_theme_button()

    def _update_theme_button(self) -> None:
        if not hasattr(self, "theme_button"):
            return
        self.theme_button.configure(text="☀" if self.dark_mode else "◑")

    def _refresh_theme_widgets(self) -> None:
        for widget_name in ("notes_text", "details_text", "ai_summary_text"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(
                    background=self.colors["field_bg"],
                    foreground=self.colors["text"],
                    insertbackground=self.colors["text"],
                )
        if hasattr(self, "ai_summary_text"):
            self._configure_ai_summary_tags()
        if hasattr(self, "watch_tree"):
            self._load_watch_items()
        if hasattr(self, "abstract_button"):
            self.abstract_button.configure(image=self.button_icons["abstract"])
        if hasattr(self, "pdf_button"):
            self.pdf_button.configure(image=self.button_icons["pdf"])

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="arXiv Secretary", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Track people, institutions, and ideas you care about. Pull a daily digest or browse the latest matches.",
            style="Muted.TLabel",
            wraplength=860,
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
        panel.rowconfigure(3, weight=1)

        ttk.Label(panel, text="Watchlist", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text="Authors use structured arXiv author search. Titles search only paper titles. Institution tracking is keyword-based because arXiv does not expose structured affiliations.",
            style="PanelMuted.TLabel",
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 12))

        form = ttk.Frame(panel, style="Panel.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)

        ttk.Label(form, text="Type").grid(row=0, column=0, sticky="w")
        self.kind_combo = ttk.Combobox(form, state="readonly", values=[kind.title() for kind in WATCH_TYPES])
        self.kind_combo.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.kind_combo.set("Author")

        ttk.Label(form, text="Search Text").grid(row=2, column=0, sticky="w")
        self.query_entry = ttk.Entry(form)
        self.query_entry.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self._install_entry_placeholder(self.query_entry, "Yann LeCun")

        ttk.Label(form, text="Label").grid(row=4, column=0, sticky="w")
        self.label_entry = ttk.Entry(form)
        self.label_entry.grid(row=5, column=0, sticky="ew", pady=(2, 8))
        self._install_entry_placeholder(self.label_entry, "Artificial Intelligence")

        ttk.Label(form, text="Notes").grid(row=6, column=0, sticky="w")
        self.notes_text = tk.Text(
            form,
            height=2,
            relief="solid",
            wrap="word",
            bd=1,
            background=self.colors["field_bg"],
            foreground=self.colors["text"],
            insertbackground=self.colors["text"],
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

        self.watch_tree = ttk.Treeview(
            panel,
            columns=("enabled", "kind", "query", "label", "last_search"),
            show="headings",
            height=14,
        )
        self.watch_tree.heading("enabled", text="")
        self.watch_tree.heading("kind", text="Type")
        self.watch_tree.heading("query", text="Target")
        self.watch_tree.heading("label", text="Label")
        self.watch_tree.heading("last_search", text="Last Search")
        self.watch_tree.column("enabled", width=34, anchor="center", stretch=False)
        self.watch_tree.column("kind", width=190, minwidth=120, anchor="center", stretch=True)
        self.watch_tree.column("query", width=190, minwidth=120, anchor="center", stretch=True)
        self.watch_tree.column("label", width=190, minwidth=120, anchor="center", stretch=True)
        self.watch_tree.column("last_search", width=190, minwidth=120, anchor="center", stretch=True)
        self.watch_tree.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.watch_tree.bind("<<TreeviewSelect>>", self._on_watch_selected)
        self.watch_tree.bind("<Button-1>", self._on_watch_tree_click)

        watch_scroll = ttk.Scrollbar(panel, orient="vertical", style="Watch.Vertical.TScrollbar", command=self.watch_tree.yview)
        self.watch_tree.configure(yscrollcommand=watch_scroll.set)
        watch_scroll.grid(row=3, column=1, sticky="ns", pady=(12, 0))

    def _build_results_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        panel.rowconfigure(4, weight=1)

        controls = ttk.Frame(panel, style="Panel.TFrame")
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(9, weight=1)

        ttk.Label(controls, text="Results", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))

        ttk.Label(controls, text="Mode").grid(row=0, column=1, sticky="w")
        mode_combo = ttk.Combobox(controls, state="readonly", textvariable=self.query_mode, values=("Latest", "Daily"))
        mode_combo.grid(row=0, column=2, sticky="w", padx=(4, 12))

        ttk.Label(controls, text="Per Watch").grid(row=0, column=3, sticky="w")
        limit_combo = ttk.Combobox(controls, width=6, textvariable=self.limit_var, values=("10", "20", "30", "50"))
        limit_combo.grid(row=0, column=4, sticky="w", padx=(4, 12))

        ttk.Label(controls, text="Daily Window").grid(row=0, column=5, sticky="w")
        days_combo = ttk.Combobox(controls, width=6, textvariable=self.daily_days_var, values=("1", "2", "3", "7"))
        days_combo.grid(row=0, column=6, sticky="w", padx=(4, 6))

        ttk.Label(controls, text="Theme").grid(row=0, column=7, sticky="w", padx=(2, 4))
        self.theme_button = ttk.Button(controls, width=3, command=self._toggle_theme)
        self.theme_button.grid(row=0, column=8, sticky="w", padx=(0, 12))
        self._update_theme_button()

        self.fetch_button = ttk.Button(controls, text="Fetch Matches", style="Accent.TButton", command=self._run_fetch)
        self.fetch_button.grid(
            row=0, column=10, sticky="e"
        )

        ttk.Label(panel, textvariable=self.selection_count_text, style="PanelMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 10)
        )

        self.results_tree = ttk.Treeview(
            panel,
            columns=("published", "title", "matches", "authors"),
            show="headings",
        )
        self._configure_result_headings()
        self.results_tree.column("published", width=112, anchor="center", stretch=False)
        self.results_tree.column("title", width=420, anchor="w", stretch=True)
        self.results_tree.column("matches", width=210, anchor="w", stretch=True)
        self.results_tree.column("authors", width=230, anchor="w", stretch=True)
        self.results_tree.grid(row=2, column=0, sticky="nsew")
        self.results_tree.bind("<<TreeviewSelect>>", self._on_result_selected)

        result_scroll = ttk.Scrollbar(panel, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=result_scroll.set)
        result_scroll.grid(row=2, column=1, sticky="ns")

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 8))
        self.abstract_button = ttk.Button(
            actions,
            text="Open Abstract",
            image=self.button_icons["abstract"],
            compound="right",
            command=self._open_abstract,
        )
        self.abstract_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.pdf_button = ttk.Button(
            actions,
            text="Open PDF",
            image=self.button_icons["pdf"],
            compound="right",
            command=self._open_pdf,
        )
        self.pdf_button.grid(row=0, column=1, sticky="w")

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
            background=self.colors["field_bg"],
            foreground=self.colors["text"],
            insertbackground=self.colors["text"],
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
                "Generate a fresh daily watchlist digest with OpenAI, Anthropic, or Google. "
                "Your API key is stored locally in arxiv_secretary.db as plain text."
            ),
            style="PanelMuted.TLabel",
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
            values=tuple(PROVIDER_LABELS.values()),
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

        ttk.Label(panel, textvariable=self.ai_summary_status_text, style="PanelMuted.TLabel").grid(
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
            background=self.colors["field_bg"],
            foreground=self.colors["text"],
            insertbackground=self.colors["text"],
        )
        self.ai_summary_text.grid(row=1, column=0, sticky="nsew")
        self._configure_ai_summary_tags()
        self.ai_summary_text.configure(state="disabled")
        self._set_ai_summary_text("No AI summary yet. Generate one from this tab to see a daily digest.")

    def _build_alerts_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        ttk.Label(panel, text="Alerts", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text="Check your enabled watch targets on a schedule and notify you when new papers are found.",
            style="PanelMuted.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(6, 14))

        settings_card = ttk.Frame(panel, style="Panel.TFrame")
        settings_card.grid(row=2, column=0, sticky="ew")
        settings_card.columnconfigure(0, weight=1)

        ttk.Checkbutton(settings_card, text="Check automatically while the app is open", variable=self.alert_enabled_var).grid(
            row=0, column=0, sticky="w"
        )

        schedule = ttk.Frame(settings_card, style="Panel.TFrame")
        schedule.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        schedule.columnconfigure(8, weight=1)

        ttk.Label(schedule, text="Run").grid(row=0, column=0, sticky="w")
        self.alert_frequency_combo = ttk.Combobox(
            schedule,
            state="readonly",
            textvariable=self.alert_frequency_var,
            values=[value.title() for value in FREQUENCIES],
            width=12,
        )
        self.alert_frequency_combo.grid(row=0, column=1, sticky="w", padx=(6, 14))

        self.alert_weekday_label = ttk.Label(schedule, text="on")
        self.alert_weekday_label.grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.alert_weekday_combo = ttk.Combobox(
            schedule,
            state="readonly",
            textvariable=self.alert_weekday_var,
            values=WEEKDAYS,
            width=14,
        )
        self.alert_weekday_combo.grid(row=0, column=3, sticky="w", padx=(0, 14))

        self.alert_month_day_label = ttk.Label(schedule, text="on day")
        self.alert_month_day_label.grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.alert_month_day_combo = ttk.Combobox(
            schedule,
            textvariable=self.alert_month_day_var,
            values=[str(value) for value in range(1, 32)],
            width=5,
        )
        self.alert_month_day_combo.grid(row=0, column=3, sticky="w", padx=(0, 14))

        ttk.Label(schedule, text="at").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.alert_hour_combo = ttk.Combobox(
            schedule,
            textvariable=self.alert_hour_var,
            values=[f"{value:02d}" for value in range(24)],
            width=5,
        )
        self.alert_hour_combo.grid(row=0, column=5, sticky="w")
        ttk.Label(schedule, text=":").grid(row=0, column=6, sticky="w", padx=2)
        self.alert_minute_combo = ttk.Combobox(
            schedule,
            textvariable=self.alert_minute_var,
            values=[f"{value:02d}" for value in range(0, 60, 5)],
            width=5,
        )
        self.alert_minute_combo.grid(row=0, column=7, sticky="w")

        notify = ttk.Frame(panel, style="Panel.TFrame")
        notify.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        notify.columnconfigure(0, weight=1)

        ttk.Label(notify, text="Notify Me", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        notify_options = ttk.Frame(notify, style="Panel.TFrame")
        notify_options.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(notify_options, text="Desktop pop-up", variable=self.alert_desktop_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(notify_options, text="Email", variable=self.alert_email_var).grid(
            row=0, column=1, sticky="w", padx=(18, 0)
        )

        self.email_detail_frame = ttk.Frame(notify, style="Panel.TFrame")
        self.email_detail_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.email_detail_frame.columnconfigure(1, weight=1)
        self.email_detail_frame.columnconfigure(3, weight=1)

        ttk.Label(self.email_detail_frame, text="Email Settings", style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        ttk.Label(self.email_detail_frame, text="SMTP Host").grid(row=1, column=0, sticky="w")
        self.smtp_host_entry = ttk.Entry(self.email_detail_frame, textvariable=self.smtp_host_var)
        self.smtp_host_entry.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=(0, 6))

        ttk.Label(self.email_detail_frame, text="Port").grid(row=1, column=2, sticky="w")
        self.smtp_port_entry = ttk.Entry(self.email_detail_frame, textvariable=self.smtp_port_var, width=8)
        self.smtp_port_entry.grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(0, 6))

        ttk.Label(self.email_detail_frame, text="Username").grid(row=2, column=0, sticky="w")
        self.smtp_username_entry = ttk.Entry(self.email_detail_frame, textvariable=self.smtp_username_var)
        self.smtp_username_entry.grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=(0, 6))

        ttk.Label(self.email_detail_frame, text="Password").grid(row=2, column=2, sticky="w")
        self.smtp_password_entry = ttk.Entry(self.email_detail_frame, textvariable=self.smtp_password_var, show="*")
        self.smtp_password_entry.grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=(0, 6))

        ttk.Label(self.email_detail_frame, text="From").grid(row=3, column=0, sticky="w")
        self.email_from_entry = ttk.Entry(self.email_detail_frame, textvariable=self.email_from_var)
        self.email_from_entry.grid(row=3, column=1, sticky="ew", padx=(8, 16), pady=(0, 6))

        ttk.Label(self.email_detail_frame, text="To").grid(row=3, column=2, sticky="w")
        self.email_to_entry = ttk.Entry(self.email_detail_frame, textvariable=self.email_to_var)
        self.email_to_entry.grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=(0, 6))

        self.smtp_tls_check = ttk.Checkbutton(self.email_detail_frame, text="Use TLS", variable=self.smtp_tls_var)
        self.smtp_tls_check.grid(row=4, column=0, sticky="w")

        footer = ttk.Frame(notify, style="Panel.TFrame")
        footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.alert_next_run_text, style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")

        button_bar = ttk.Frame(footer, style="Panel.TFrame")
        button_bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(button_bar, text="Save Settings", command=self._save_alert_settings).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Button(button_bar, text="Run Now", style="Accent.TButton", command=self._run_alert_now).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(footer, textvariable=self.alert_status_text, style="PanelMuted.TLabel", wraplength=980, justify="left").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

    def _configure_ai_summary_tags(self) -> None:
        self.ai_summary_text.tag_configure("md_body", foreground=self.colors["text"], spacing1=2, spacing3=2)
        self.ai_summary_text.tag_configure(
            "md_h1",
            font=("Segoe UI", 15, "bold"),
            foreground=self.colors["heading"],
            spacing1=8,
            spacing3=6,
        )
        self.ai_summary_text.tag_configure(
            "md_h2",
            font=("Segoe UI", 13, "bold"),
            foreground=self.colors["section"],
            spacing1=8,
            spacing3=4,
        )
        self.ai_summary_text.tag_configure(
            "md_h3",
            font=("Segoe UI", 12, "bold"),
            foreground=self.colors["section"],
            spacing1=6,
            spacing3=3,
        )
        self.ai_summary_text.tag_configure("md_bold", font=("Segoe UI", 10, "bold"))
        self.ai_summary_text.tag_configure(
            "md_bullet",
            foreground=self.colors["text"],
            lmargin1=16,
            lmargin2=32,
            spacing1=2,
            spacing3=2,
        )
        self.ai_summary_text.tag_configure(
            "md_rule",
            foreground=self.colors["muted"],
            justify="center",
            spacing1=6,
            spacing3=6,
        )

    def _install_entry_placeholder(self, entry: ttk.Entry, placeholder: str) -> None:
        entry.placeholder_text = placeholder  # type: ignore[attr-defined]
        entry.placeholder_visible = False  # type: ignore[attr-defined]
        self._show_entry_placeholder(entry)
        entry.bind("<FocusIn>", lambda _event, widget=entry: self._hide_entry_placeholder(widget), add="+")
        entry.bind("<FocusOut>", lambda _event, widget=entry: self._show_entry_placeholder(widget), add="+")

    def _show_entry_placeholder(self, entry: ttk.Entry) -> None:
        if entry.get():
            return
        placeholder = getattr(entry, "placeholder_text", "")
        if not placeholder:
            return
        entry.placeholder_visible = True  # type: ignore[attr-defined]
        entry.configure(foreground=self.colors["placeholder"])
        entry.insert(0, placeholder)

    def _hide_entry_placeholder(self, entry: ttk.Entry) -> None:
        if not getattr(entry, "placeholder_visible", False):
            return
        entry.delete(0, "end")
        entry.placeholder_visible = False  # type: ignore[attr-defined]
        entry.configure(foreground=self.colors["text"])

    def _entry_value(self, entry: ttk.Entry) -> str:
        if getattr(entry, "placeholder_visible", False):
            return ""
        return entry.get().strip()

    def _set_entry_value(self, entry: ttk.Entry, value: str) -> None:
        self._hide_entry_placeholder(entry)
        entry.delete(0, "end")
        entry.configure(foreground=self.colors["text"])
        if value:
            entry.insert(0, value)
        else:
            self._show_entry_placeholder(entry)

    def _restore_defaults(self) -> None:
        self.kind_combo.set("Author")

    def _provider_key(self) -> str:
        value = self.ai_provider_var.get().strip()
        for key, label in PROVIDER_LABELS.items():
            if value.casefold() in {key.casefold(), label.casefold()}:
                return key
        return ""

    def _provider_label(self, provider: str) -> str:
        key = provider.strip().lower()
        return PROVIDER_LABELS.get(key, provider.strip() or PROVIDER_LABELS["openai"])

    def _load_ai_settings(self) -> None:
        provider = self._provider_key() or "openai"
        if provider not in DEFAULT_MODELS:
            provider = "openai"
        provider_label = self._provider_label(provider)
        if self.ai_provider_var.get() != provider_label:
            self.ai_provider_var.set(provider_label)
        self.ai_model_var.set(self.storage.get_setting(f"ai_{provider}_model", DEFAULT_MODELS[provider]))
        self.ai_key_var.set(self.storage.get_setting(f"ai_{provider}_api_key", ""))

    def _load_alert_settings(self) -> None:
        self.alert_enabled_var.set(self.storage.get_setting("alert_enabled", "1") == "1")
        self.alert_frequency_var.set(self.storage.get_setting("alert_frequency", "daily").title())
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
        provider = self._provider_key()
        if provider not in DEFAULT_MODELS:
            messagebox.showerror("Invalid provider", "Choose OpenAI, Anthropic, or Google.")
            return
        self.storage.set_setting("ai_provider", provider)
        self.storage.set_setting(f"ai_{provider}_model", self.ai_model_var.get().strip())
        self.storage.set_setting(f"ai_{provider}_api_key", self.ai_key_var.get().strip())
        self.ai_summary_status_text.set(f"Saved {self._provider_label(provider)} settings locally.")

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
        alerts_enabled = self.alert_enabled_var.get()
        common_state = "normal" if alerts_enabled else "disabled"
        combo_state = "readonly" if alerts_enabled else "disabled"
        weekly_state = "readonly" if alerts_enabled and frequency == "weekly" else "disabled"
        monthly_state = "normal" if alerts_enabled and frequency == "monthly" else "disabled"
        self.alert_frequency_combo.configure(state=combo_state)
        self.alert_hour_combo.configure(state=common_state)
        self.alert_minute_combo.configure(state=common_state)
        self.alert_weekday_combo.configure(state=weekly_state)
        self.alert_month_day_combo.configure(state=monthly_state)
        if frequency == "weekly":
            self.alert_weekday_label.grid()
            self.alert_weekday_combo.grid()
            self.alert_month_day_label.grid_remove()
            self.alert_month_day_combo.grid_remove()
        elif frequency == "monthly":
            self.alert_month_day_label.grid()
            self.alert_month_day_combo.grid()
            self.alert_weekday_label.grid_remove()
            self.alert_weekday_combo.grid_remove()
        else:
            self.alert_weekday_label.grid_remove()
            self.alert_weekday_combo.grid_remove()
            self.alert_month_day_label.grid_remove()
            self.alert_month_day_combo.grid_remove()

        email_state = "normal" if alerts_enabled and self.alert_email_var.get() else "disabled"
        for widget in (
            self.smtp_host_entry,
            self.smtp_port_entry,
            self.smtp_username_entry,
            self.smtp_password_entry,
            self.email_from_entry,
            self.email_to_entry,
        ):
            widget.configure(state=email_state)
        self.smtp_tls_check.configure(state=email_state)

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
            label = "" if item.label.strip().casefold() == item.query.strip().casefold() else item.label
            enabled = "☑" if item.enabled else "☐"
            last_search = self._format_last_search(item.last_search_at)
            self.watch_tree.insert(
                "",
                "end",
                iid=str(item.id),
                values=(enabled, item.kind.title(), item.query, label, last_search),
            )

    def _format_last_search(self, value: str) -> str:
        if not value:
            return "N/A"
        try:
            searched_at = datetime.fromisoformat(value)
            if searched_at.tzinfo is None:
                searched_at = searched_at.replace(tzinfo=UTC)
            return searched_at.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value

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

    def _on_watch_tree_click(self, event: object) -> str | None:
        column = self.watch_tree.identify_column(getattr(event, "x"))
        row = self.watch_tree.identify_row(getattr(event, "y"))
        if column == "#1" and row:
            self.selected_watch_id = int(row)
            self.watch_tree.selection_set(row)
            self._toggle_selected_watch_item()
            return "break"
        return None

    def _toggle_selected_watch_item(self, _event: object | None = None) -> None:
        event_row = ""
        if _event is not None and hasattr(_event, "y"):
            event_row = self.watch_tree.identify_row(getattr(_event, "y"))
        if event_row:
            self.selected_watch_id = int(event_row)
            self.watch_tree.selection_set(event_row)
        if self.selected_watch_id is None:
            selection = self.watch_tree.selection()
            if selection:
                self.selected_watch_id = int(selection[0])
            else:
                messagebox.showinfo("Select a watch", "Choose a saved watch target to enable or disable.")
                return
        selected = self._find_watch_by_id(self.selected_watch_id)
        if selected is None:
            return
        new_enabled = not selected.enabled
        self.storage.set_watch_enabled(self.selected_watch_id, new_enabled)
        self._load_watch_items()
        self.watch_tree.selection_set(str(self.selected_watch_id))
        self._on_watch_selected(None)
        state = "enabled" if new_enabled else "disabled"
        self.status_text.set(f"{selected.label} is now {state}.")

    def _collect_form_item(self, *, existing_id: int | None) -> WatchItem | None:
        kind = self.kind_combo.get().strip().lower()
        query = self._entry_value(self.query_entry)
        label = self._entry_value(self.label_entry) or query
        notes = self.notes_text.get("1.0", "end").strip()
        existing = self._find_watch_by_id(existing_id) if existing_id is not None else None
        enabled = existing.enabled if existing is not None else True
        last_search_at = existing.last_search_at if existing is not None else ""
        if kind not in WATCH_TYPES:
            messagebox.showerror("Invalid type", "Choose Author, Institution, Topic, or Title.")
            return None
        if not query:
            messagebox.showerror("Missing search text", "Search text is required. Label is optional.")
            return None
        return WatchItem(
            id=existing_id,
            kind=kind,
            label=label,
            query=query,
            notes=notes,
            enabled=enabled,
            last_search_at=last_search_at,
        )

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
        self._set_entry_value(self.query_entry, item.query)
        label = "" if item.label.strip().casefold() == item.query.strip().casefold() else item.label
        self._set_entry_value(self.label_entry, label)
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", item.notes)

    def _clear_form(self) -> None:
        self.selected_watch_id = None
        self.kind_combo.set("Author")
        self._set_entry_value(self.query_entry, "")
        self._set_entry_value(self.label_entry, "")
        self.notes_text.delete("1.0", "end")
        for selected in self.watch_tree.selection():
            self.watch_tree.selection_remove(selected)

    def _find_watch_by_id(self, item_id: int) -> WatchItem | None:
        for item in self.watch_items:
            if item.id == item_id:
                return item
        return None

    def _active_watch_items(self) -> list[WatchItem]:
        return [item for item in self.storage.list_watch_items() if item.enabled]

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
        active_items = [item for item in watch_items if item.enabled]
        for index, item in enumerate(active_items):
            if index > 0:
                sleep(3)
            try:
                papers = fetch_matches(
                    item,
                    max_results=max_results,
                    only_last_days=daily_days if mode == "daily" else None,
                )
            except Exception as exc:
                failures.append(f"{item.label}: {exc}")
                continue
            finally:
                if item.id is not None:
                    self.storage.set_watch_last_search(item.id, datetime.now(UTC).isoformat())

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
        watch_items = self._active_watch_items()
        if not watch_items:
            messagebox.showinfo("No enabled watches", "Enable at least one watch target before fetching matches.")
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
            messagebox.showerror("Invalid mode", "Choose either Latest or Daily.")
            return

        self.fetch_request_id += 1
        request_id = self.fetch_request_id
        self.status_text.set(f"Fetching arXiv matches for {len(watch_items)} enabled target(s)...")
        self.selection_count_text.set("Loading papers...")
        self.fetch_button.configure(state="disabled")
        self.executor.submit(self._fetch_worker, request_id, watch_items, mode, max_results, daily_days)

    def _run_ai_summary(self) -> None:
        watch_items = self._active_watch_items()
        if not watch_items:
            messagebox.showinfo("No enabled watches", "Enable at least one watch target before generating a summary.")
            return
        provider = self._provider_key()
        if provider not in DEFAULT_MODELS:
            messagebox.showerror("Invalid provider", "Choose OpenAI, Anthropic, or Google.")
            return

        api_key = self.ai_key_var.get().strip()
        model = self.ai_model_var.get().strip()
        if not api_key:
            messagebox.showerror("Missing API key", f"Enter a {self._provider_label(provider)} API key first.")
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
        watch_items = self._active_watch_items()
        if not watch_items:
            messagebox.showinfo("No enabled watches", "Enable at least one watch target before running an alert.")
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

    def _fetch_worker(
        self,
        request_id: int,
        watch_items: list[WatchItem],
        mode: str,
        max_results: int,
        daily_days: int,
    ) -> None:
        try:
            ordered, failures = self._collect_feed_results(
                watch_items,
                mode=mode,
                max_results=max_results,
                daily_days=daily_days,
            )
            self.queue.put(
                (
                    "fetch_success",
                    {
                        "request_id": request_id,
                        "mode": mode,
                        "papers": ordered,
                        "failures": failures,
                    },
                )
            )
        except Exception as exc:
            self.queue.put(("fetch_error", {"request_id": request_id, "error": exc}))

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
                    data = payload if isinstance(payload, dict) else {}
                    request_id = data.get("request_id")
                    if request_id != self.fetch_request_id:
                        continue
                    error = data.get("error", payload)
                    self.status_text.set("Fetch failed")
                    self.fetch_button.configure(state="normal")
                    messagebox.showerror("Fetch failed", str(error))
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
                watch_items = self._active_watch_items()
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
        request_id = data.get("request_id") if isinstance(data, dict) else None
        if request_id is not None and request_id != self.fetch_request_id:
            return
        papers = data.get("papers", []) if isinstance(data, dict) else []
        failures = data.get("failures", []) if isinstance(data, dict) else []
        mode = data.get("mode", "latest") if isinstance(data, dict) else "latest"
        self.results = list(papers)
        self.result_index = {paper.entry_id: paper for paper in self.results}
        self._sort_results(self.results_sort_column, keep_direction=True)
        if request_id is not None:
            self.fetch_button.configure(state="normal")

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

        self._load_watch_items()

        if failures:
            preview = "\n".join(failures[:5])
            if len(failures) > 5:
                preview += f"\n...and {len(failures) - 5} more."
            messagebox.showwarning("Some watches failed", preview)

    def _configure_result_headings(self) -> None:
        labels = {
            "published": "Published",
            "title": "Paper",
            "matches": "Matched Watches",
            "authors": "Authors",
        }
        for column, label in labels.items():
            indicator = ""
            if column == self.results_sort_column:
                indicator = " ⮟" if self.results_sort_reverse else " ⮝"
            self.results_tree.heading(
                column,
                text=f"{label}{indicator}",
                command=lambda value=column: self._sort_results(value),
            )

    def _sort_results(self, column: str, *, keep_direction: bool = False) -> None:
        if not keep_direction:
            if self.results_sort_column == column:
                self.results_sort_reverse = not self.results_sort_reverse
            else:
                self.results_sort_column = column
                self.results_sort_reverse = column == "published"

        def sort_key(paper: Paper) -> object:
            if column == "published":
                try:
                    return parse_arxiv_datetime(paper.published)
                except Exception:
                    return datetime.min.replace(tzinfo=UTC)
            if column == "title":
                return paper.title.casefold()
            if column == "matches":
                return ", ".join(sorted(paper.matched_watch_labels)).casefold()
            if column == "authors":
                return paper.short_authors.casefold()
            return paper.title.casefold()

        self.results.sort(key=sort_key, reverse=self.results_sort_reverse)
        self._configure_result_headings()
        self._refresh_results_tree()

    def _refresh_results_tree(self) -> None:
        selected = self.results_tree.selection()
        selected_id = selected[0] if selected else ""
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
        if selected_id in self.result_index:
            self.results_tree.selection_set(selected_id)
            self.results_tree.focus(selected_id)

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
        provider_label = self._provider_label(provider) if isinstance(provider, str) else "AI"
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
                widget.insert("end", "----------------------------------------\n", ("md_rule",))
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
                widget.insert("end", "* ", ("md_bullet",))
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
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        ArxivSecretaryApp(root)
        root.mainloop()
    except Exception as exc:
        _write_startup_crash_log(exc)
        if root is None:
            try:
                root = tk.Tk()
                root.withdraw()
            except Exception:
                root = None

        if root is not None:
            try:
                messagebox.showerror(
                    "arXiv Secretary could not start",
                    "The app hit a startup problem and wrote a crash log to your local app data folder.",
                )
            except Exception:
                pass
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        return


def _write_startup_crash_log(exc: Exception) -> None:
    try:
        log_path = app_data_dir() / "startup-error.log"
        timestamp = datetime.now().astimezone().isoformat()
        log_path.write_text(
            f"[{timestamp}] Startup failure: {exc}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass
