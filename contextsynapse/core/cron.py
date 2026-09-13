"""Cron Scheduler — core scheduling engine for contextsynapse.

Evaluates cron expressions to determine if a task is due to run.
Used by pipeline scheduler, content monitor, NAV engine, report generator,
and any other component that needs time-based scheduling.

Supported formats:
  ┌─────────── minute (0-59)
  │ ┌───────── hour (0-23)
  │ │ ┌─────── day of month (1-31)
  │ │ │ ┌───── month (1-12)
  │ │ │ │ ┌─── day of week (0-6, Monday=0)
  │ │ │ │ │
  * * * * *

  Patterns per field:
    *        any value
    5        exact match
    1,3,5    list of values
    1-5      range (inclusive)
    */10     every Nth value from zero
    1-5/2    range with step
    9-17     range (e.g., market hours 9 AM to 5 PM)

  Named shortcuts:
    @hourly    → 0 * * * *        top of every hour
    @daily     → 0 0 * * *        midnight
    @weekly    → 0 0 * * 0        Sunday midnight
    @monthly   → 0 0 1 * *        1st of month, midnight

  Interval shorthands:
    5m         every 5 minutes
    30m        every 30 minutes
    6h         every 6 hours

  Examples:
    "*/30 * * * *"        every 30 minutes
    "0 9,15 * * 1-5"      9 AM and 3 PM, weekdays only
    "45 15 * * 1-5"       3:45 PM weekdays (Indian market close)
    "0 6 * * *"           6 AM daily (pre-market prep)
    "0 0 1 * *"           midnight, 1st of month (monthly reports)
    "*/5 9-16 * * 1-5"    every 5 min during market hours, weekdays
    "0 */6 * * *"         every 6 hours

Usage:
    from contextsynapse.core.cron import CronSchedule

    schedule = CronSchedule("*/30 9-16 * * 1-5")
    if schedule.is_due(last_run_iso="2026-09-12T09:00:00Z"):
        run_pipeline()

    # Human-readable description
    print(schedule.describe())
    # → "Every 30 minutes, hours 9-16, Monday to Friday"
"""
from datetime import datetime, timezone
from typing import Optional

import logging

logger = logging.getLogger(__name__)


# Named shortcuts → standard 5-field cron
SHORTCUTS = {
    "@hourly":  "0 * * * *",
    "@daily":   "0 0 * * *",
    "@weekly":  "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "hourly":   "0 * * * *",
    "daily":    "0 0 * * *",
    "weekly":   "0 0 * * 0",
    "monthly":  "0 0 1 * *",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _parse_iso(iso_str: str) -> Optional[datetime]:
    """Parse an ISO timestamp string into a timezone-aware UTC datetime."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime has UTC timezone."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def field_matches(expr: str, value: int) -> bool:
    """Check if a single cron field expression matches a value.

    Supports:
      *        any value
      5        exact match
      1,3,5    list of values
      1-5      range (inclusive)
      */10     every Nth value from zero
      1-5/2    range with step
    """
    for token in expr.split(","):
        token = token.strip()

        # Wildcard
        if token == "*":
            return True

        # Step from zero: */N
        if token.startswith("*/"):
            try:
                step = int(token[2:])
                if step > 0 and value % step == 0:
                    return True
            except ValueError:
                pass
            continue

        # Range with step: A-B/N
        if "-" in token and "/" in token:
            try:
                range_part, step_str = token.split("/", 1)
                lo, hi = range_part.split("-", 1)
                lo, hi, step = int(lo), int(hi), int(step_str)
                if lo <= value <= hi and (value - lo) % step == 0:
                    return True
            except ValueError:
                pass
            continue

        # Range: A-B
        if "-" in token:
            try:
                lo, hi = token.split("-", 1)
                if int(lo) <= value <= int(hi):
                    return True
            except ValueError:
                pass
            continue

        # Exact value
        try:
            if int(token) == value:
                return True
        except ValueError:
            pass

    return False


def is_due(cron_expr: str, last_run_iso: str = "", now: datetime = None) -> bool:
    """Check if a cron schedule is due to run.

    Args:
        cron_expr: cron expression, shortcut, or interval shorthand
        last_run_iso: ISO timestamp of last run (empty = never ran)
        now: current time (defaults to UTC now)

    Returns:
        True if the task should run now
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now = _ensure_utc(now)

    expr = cron_expr.strip()

    # ── Interval shorthands: "30m", "6h" ──
    if expr.endswith("m") and expr[:-1].isdigit():
        last = _parse_iso(last_run_iso)
        if last is None:
            return True
        return (now - last).total_seconds() >= int(expr[:-1]) * 60

    if expr.endswith("h") and expr[:-1].isdigit():
        last = _parse_iso(last_run_iso)
        if last is None:
            return True
        return (now - last).total_seconds() >= int(expr[:-1]) * 3600

    # ── Named shortcuts ──
    expr = SHORTCUTS.get(expr, expr)

    # ── Standard 5-field cron ──
    fields = expr.split()
    if len(fields) != 5:
        logger.warning("[CRON] Invalid expression '%s' — running anyway", cron_expr)
        return True

    minute_expr, hour_expr, dom_expr, month_expr, dow_expr = fields

    time_matches = (
        field_matches(minute_expr, now.minute) and
        field_matches(hour_expr, now.hour) and
        field_matches(dom_expr, now.day) and
        field_matches(month_expr, now.month) and
        field_matches(dow_expr, now.weekday())  # Monday=0
    )

    if not time_matches:
        return False

    # Don't run twice in the same matching minute
    last = _parse_iso(last_run_iso)
    if last is not None and (now - last).total_seconds() < 60:
        return False

    return True


def describe(cron_expr: str) -> str:
    """Human-readable description of a cron expression.

    Examples:
        "*/30 * * * *"     → "Every 30 minutes"
        "0 9,15 * * 1-5"   → "At 09:00 and 15:00, Monday to Friday"
        "45 15 * * 1-5"    → "At 15:45, Monday to Friday"
        "30m"              → "Every 30 minutes"
        "@daily"           → "Daily at midnight"
    """
    expr = cron_expr.strip()

    # Shorthands
    if expr.endswith("m") and expr[:-1].isdigit():
        return f"Every {expr[:-1]} minutes"
    if expr.endswith("h") and expr[:-1].isdigit():
        return f"Every {expr[:-1]} hours"
    if expr in SHORTCUTS:
        labels = {
            "@hourly": "Every hour", "hourly": "Every hour",
            "@daily": "Daily at midnight", "daily": "Daily at midnight",
            "@weekly": "Weekly on Sunday", "weekly": "Weekly on Sunday",
            "@monthly": "Monthly on the 1st", "monthly": "Monthly on the 1st",
        }
        return labels.get(expr, expr)

    fields = expr.split()
    if len(fields) != 5:
        return expr

    minute_expr, hour_expr, dom_expr, month_expr, dow_expr = fields
    parts = []

    # Time
    if minute_expr.startswith("*/"):
        parts.append(f"Every {minute_expr[2:]} minutes")
    elif minute_expr == "*":
        parts.append("Every minute")
    else:
        if hour_expr == "*":
            parts.append(f"At minute {minute_expr}")
        else:
            hours = hour_expr.split(",")
            times = [f"{h.zfill(2)}:{minute_expr.zfill(2)}" for h in hours]
            parts.append(f"At {', '.join(times)}")

    # Hour constraint (if not already covered)
    if not minute_expr.startswith("*/") and hour_expr != "*" and "," not in hour_expr:
        if "-" in hour_expr:
            lo, hi = hour_expr.split("-", 1)
            parts.append(f"hours {lo}-{hi}")

    # Day of week
    if dow_expr != "*":
        if dow_expr == "1-5":
            parts.append("Monday to Friday")
        elif dow_expr == "0-4":
            parts.append("Monday to Friday")
        elif dow_expr == "6,0":
            parts.append("weekends")
        else:
            try:
                days = []
                for d in dow_expr.split(","):
                    d = d.strip()
                    if "-" in d:
                        lo, hi = d.split("-")
                        days.append(f"{DAY_NAMES[int(lo)]}-{DAY_NAMES[int(hi)]}")
                    else:
                        days.append(DAY_NAMES[int(d)])
                parts.append(", ".join(days))
            except (ValueError, IndexError):
                parts.append(f"dow={dow_expr}")

    # Day of month
    if dom_expr != "*":
        parts.append(f"day {dom_expr}")

    # Month
    if month_expr != "*":
        try:
            months = [MONTH_NAMES[int(m)] for m in month_expr.split(",")]
            parts.append(", ".join(months))
        except (ValueError, IndexError):
            parts.append(f"month={month_expr}")

    return ", ".join(parts)


class CronSchedule:
    """Object-oriented wrapper around cron expression evaluation.

    Usage:
        schedule = CronSchedule("*/30 9-16 * * 1-5")
        schedule.is_due(last_run_iso="2026-09-12T09:00:00Z")
        schedule.describe()  # "Every 30 minutes, hours 9-16, Monday to Friday"
    """

    def __init__(self, expr: str):
        self.expr = expr.strip()

    def is_due(self, last_run_iso: str = "", now: datetime = None) -> bool:
        return is_due(self.expr, last_run_iso, now)

    def describe(self) -> str:
        return describe(self.expr)

    def __repr__(self):
        return f"CronSchedule('{self.expr}') → {self.describe()}"
