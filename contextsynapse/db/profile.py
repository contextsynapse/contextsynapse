"""ProfileStore — fund manager profile CRUD backed by PostgreSQL."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from contextsynapse.db import execute, execute_one, insert, update
from contextsynapse.db.postgres import _USE_PG

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON helpers (PG auto-parses JSONB, SQLite stores as TEXT)
# ---------------------------------------------------------------------------

_JSON_FIELDS = ("preferred_sectors", "notification_preferences", "dashboard_layout")


def _serialize_json(value: Any) -> str:
    """Serialize a Python object to JSON string for storage."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json(value: Any, default=None) -> Any:
    """Parse a JSON string back to Python objects."""
    if value is None:
        return default if default is not None else []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default if default is not None else []
    return value


def _hydrate_profile(row: dict) -> Optional[dict]:
    """Convert a raw DB row into a clean profile dict with parsed JSON fields."""
    if row is None:
        return None
    profile = dict(row)
    profile["preferred_sectors"] = _parse_json(profile.get("preferred_sectors"), [])
    profile["notification_preferences"] = _parse_json(
        profile.get("notification_preferences"),
        {"email": True, "sms": False, "push": True},
    )
    profile["dashboard_layout"] = _parse_json(profile.get("dashboard_layout"), {})
    # Convert numeric fields that may come back as Decimal
    for field in ("total_aum", "best_year_return", "avg_annual_return"):
        if profile.get(field) is not None:
            profile[field] = float(profile[field])
    # Stringify dates for JSON serialization
    for field in ("created_at", "updated_at", "since_date"):
        val = profile.get(field)
        if val is not None and not isinstance(val, str):
            profile[field] = str(val)
    return profile


class ProfileStore:
    """PostgreSQL / SQLite-backed fund manager profile storage."""

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_profile(
        self,
        user_id: str,
        tenant_id: str,
        display_name: str,
        **kwargs,
    ) -> dict:
        """Insert a new fund manager profile and return it."""
        data = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "display_name": display_name,
        }

        # Merge optional fields
        allowed = {
            "title", "bio", "avatar_url", "phone", "linkedin_url",
            "sebi_registration_no", "arn_number", "nism_certification",
            "experience_years", "specialization", "investment_philosophy",
            "total_aum", "portfolios_managed", "schemes_managed",
            "clients_served", "since_date", "best_year_return",
            "avg_annual_return", "preferred_sectors", "risk_appetite",
            "benchmark", "notification_preferences", "dashboard_layout",
            "timezone",
        }
        for key, val in kwargs.items():
            if key in allowed and val is not None:
                if key in _JSON_FIELDS:
                    data[key] = _serialize_json(val)
                else:
                    data[key] = val

        row = insert("fund_manager_profiles", data)
        return _hydrate_profile(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_profile(self, user_id: str) -> Optional[dict]:
        """Get a profile by user_id, or None."""
        row = execute_one(
            "SELECT * FROM fund_manager_profiles WHERE user_id = %s",
            (user_id,),
        )
        return _hydrate_profile(row)

    def get_profile_by_id(self, profile_id: str) -> Optional[dict]:
        """Get a profile by its own primary key id."""
        row = execute_one(
            "SELECT * FROM fund_manager_profiles WHERE id = %s",
            (profile_id,),
        )
        return _hydrate_profile(row)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_profile(self, user_id: str, **updates) -> Optional[dict]:
        """Update a profile and return the updated version."""
        if not updates:
            return self.get_profile(user_id)

        data = {}
        for key, val in updates.items():
            if val is None:
                continue
            if key in _JSON_FIELDS:
                data[key] = _serialize_json(val)
            else:
                data[key] = val

        if not data:
            return self.get_profile(user_id)

        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        affected = update(
            "fund_manager_profiles",
            data,
            "user_id = %s",
            (user_id,),
        )
        if affected == 0:
            log.warning("Profile update matched 0 rows for user_id=%s", user_id)
            return None

        return self.get_profile(user_id)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_profile(self, user_id: str) -> bool:
        """Delete a profile by user_id. Returns True if a row was deleted."""
        rows = execute(
            "DELETE FROM fund_manager_profiles WHERE user_id = %s",
            (user_id,),
        )
        # execute returns list of dicts; for DELETE it's typically empty
        # We rely on the fact that if no error was raised, the delete succeeded
        # Check by trying to fetch
        return self.get_profile(user_id) is None

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_profiles(self, tenant_id: str) -> List[dict]:
        """List all profiles in a tenant."""
        rows = execute(
            "SELECT * FROM fund_manager_profiles WHERE tenant_id = %s ORDER BY display_name",
            (tenant_id,),
        )
        return [_hydrate_profile(r) for r in rows]

    # ------------------------------------------------------------------
    # Performance stats (auto-compute)
    # ------------------------------------------------------------------

    def update_performance_stats(
        self,
        user_id: str,
        total_aum: Optional[float] = None,
        portfolios_managed: Optional[int] = None,
        clients_served: Optional[int] = None,
        schemes_managed: Optional[int] = None,
        best_year_return: Optional[float] = None,
        avg_annual_return: Optional[float] = None,
    ) -> Optional[dict]:
        """Update computed performance stats on a profile."""
        data = {}
        if total_aum is not None:
            data["total_aum"] = total_aum
        if portfolios_managed is not None:
            data["portfolios_managed"] = portfolios_managed
        if clients_served is not None:
            data["clients_served"] = clients_served
        if schemes_managed is not None:
            data["schemes_managed"] = schemes_managed
        if best_year_return is not None:
            data["best_year_return"] = best_year_return
        if avg_annual_return is not None:
            data["avg_annual_return"] = avg_annual_return

        if not data:
            return self.get_profile(user_id)

        return self.update_profile(user_id, **data)

    # ------------------------------------------------------------------
    # Get or create
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        user_id: str,
        tenant_id: str,
        display_name: str,
    ) -> dict:
        """Get existing profile or create with defaults."""
        existing = self.get_profile(user_id)
        if existing:
            return existing
        return self.create_profile(user_id, tenant_id, display_name)
