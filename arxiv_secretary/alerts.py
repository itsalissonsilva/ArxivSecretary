from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.message import EmailMessage
import smtplib

from .models import Paper


FREQUENCIES = ("daily", "weekly", "monthly")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(slots=True)
class AlertSettings:
    enabled: bool = True
    frequency: str = "daily"
    hour: int = 12
    minute: int = 0
    weekday: int = 0
    month_day: int = 1
    desktop_enabled: bool = True
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = ""
    email_to: str = ""


def normalize_alert_settings(settings: AlertSettings) -> AlertSettings:
    frequency = settings.frequency if settings.frequency in FREQUENCIES else "daily"
    hour = min(max(int(settings.hour), 0), 23)
    minute = min(max(int(settings.minute), 0), 59)
    weekday = min(max(int(settings.weekday), 0), 6)
    month_day = min(max(int(settings.month_day), 1), 31)
    smtp_port = max(int(settings.smtp_port), 1)
    return AlertSettings(
        enabled=bool(settings.enabled),
        frequency=frequency,
        hour=hour,
        minute=minute,
        weekday=weekday,
        month_day=month_day,
        desktop_enabled=bool(settings.desktop_enabled),
        email_enabled=bool(settings.email_enabled),
        smtp_host=settings.smtp_host.strip(),
        smtp_port=smtp_port,
        smtp_username=settings.smtp_username.strip(),
        smtp_password=settings.smtp_password,
        smtp_use_tls=bool(settings.smtp_use_tls),
        email_from=settings.email_from.strip(),
        email_to=settings.email_to.strip(),
    )


def should_run_now(settings: AlertSettings, now: datetime, last_run_marker: str) -> tuple[bool, str]:
    normalized = normalize_alert_settings(settings)
    if not normalized.enabled:
        return False, ""
    scheduled = current_period_run_datetime(normalized, now)
    marker = marker_for_datetime(normalized, scheduled)
    if now < scheduled:
        return False, marker
    if last_run_marker == marker:
        return False, marker
    return True, marker


def current_period_run_datetime(settings: AlertSettings, now: datetime) -> datetime:
    local_now = now.astimezone()
    run_time = time(settings.hour, settings.minute, tzinfo=local_now.tzinfo)
    if settings.frequency == "daily":
        return datetime.combine(local_now.date(), run_time)
    if settings.frequency == "weekly":
        start_of_week = local_now.date() - timedelta(days=local_now.weekday())
        target_date = start_of_week + timedelta(days=settings.weekday)
        return datetime.combine(target_date, run_time)

    last_day = monthrange(local_now.year, local_now.month)[1]
    target_day = min(settings.month_day, last_day)
    return datetime(local_now.year, local_now.month, target_day, settings.hour, settings.minute, tzinfo=local_now.tzinfo)


def next_run_datetime(settings: AlertSettings, now: datetime) -> datetime:
    normalized = normalize_alert_settings(settings)
    current = current_period_run_datetime(normalized, now)
    if now < current:
        return current
    local_now = now.astimezone()
    if normalized.frequency == "daily":
        return current + timedelta(days=1)
    if normalized.frequency == "weekly":
        return current + timedelta(days=7)

    year = local_now.year
    month = local_now.month + 1
    if month == 13:
        month = 1
        year += 1
    last_day = monthrange(year, month)[1]
    target_day = min(normalized.month_day, last_day)
    return datetime(year, month, target_day, normalized.hour, normalized.minute, tzinfo=local_now.tzinfo)


def marker_for_datetime(settings: AlertSettings, value: datetime) -> str:
    if settings.frequency == "daily":
        return value.strftime("daily:%Y-%m-%d")
    if settings.frequency == "weekly":
        return value.strftime("weekly:%Y-%m-%d")
    return value.strftime("monthly:%Y-%m")


def describe_schedule(settings: AlertSettings, now: datetime) -> str:
    normalized = normalize_alert_settings(settings)
    next_run = next_run_datetime(normalized, now)
    if normalized.frequency == "daily":
        label = "Daily"
    elif normalized.frequency == "weekly":
        label = f"Weekly on {WEEKDAYS[normalized.weekday]}"
    else:
        label = f"Monthly on day {normalized.month_day}"
    return f"{label} at {normalized.hour:02d}:{normalized.minute:02d}. Next run: {next_run.strftime('%Y-%m-%d %H:%M')}"


def build_notification_message(papers: list[Paper], *, mode: str) -> str:
    if not papers:
        if mode == "daily":
            return "No new papers were found in the daily window."
        return "No matching papers were found in the latest feed."

    lines = [
        f"{len(papers)} paper(s) found for the {mode} alert.",
        "",
    ]
    for paper in papers[:5]:
        matches = ", ".join(sorted(paper.matched_watch_labels)) or "watchlist"
        lines.append(f"- {paper.title} [{matches}]")
    if len(papers) > 5:
        lines.append(f"- ...and {len(papers) - 5} more")
    return "\n".join(lines)


def send_email_notification(
    settings: AlertSettings,
    *,
    subject: str,
    body: str,
) -> None:
    normalized = normalize_alert_settings(settings)
    if not normalized.email_enabled:
        return
    missing = []
    if not normalized.smtp_host:
        missing.append("SMTP host")
    if not normalized.smtp_username:
        missing.append("SMTP username")
    if not normalized.smtp_password:
        missing.append("SMTP password")
    if not normalized.email_from:
        missing.append("From email")
    if not normalized.email_to:
        missing.append("To email")
    if missing:
        raise ValueError(f"Email alerts are enabled, but these fields are missing: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = normalized.email_from
    message["To"] = normalized.email_to
    message.set_content(body)

    with smtplib.SMTP(normalized.smtp_host, normalized.smtp_port, timeout=30) as server:
        if normalized.smtp_use_tls:
            server.starttls()
        server.login(normalized.smtp_username, normalized.smtp_password)
        server.send_message(message)
