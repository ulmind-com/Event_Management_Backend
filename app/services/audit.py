from app.core.database import audit_log_collection
from datetime import datetime, timezone

async def log_admin_action(
    actor_email: str,
    action: str,
    resource_type: str,
    resource_id: str = None,
    outcome: str = "success",
    metadata: dict = None
):
    """Record an admin action in the audit log. Never log secrets."""
    entry = {
        "actor": actor_email,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc),
    }
    if metadata:
        # Strip any sensitive keys just in case
        safe_meta = {k: v for k, v in metadata.items() if k.lower() not in ("password", "token", "secret", "otp")}
        entry["metadata"] = safe_meta
    await audit_log_collection.insert_one(entry)
