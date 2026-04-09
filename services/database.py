"""
services/database.py - Supabase client wrapper with all DB operations
"""
from __future__ import annotations
import logging
from datetime import date, datetime
from typing import Any, Optional
from supabase import create_client, Client
from config import Config

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_db() -> Client:
    """Lazily initialize and return the Supabase client (service role)."""
    global _client
    if _client is None:
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    return _client


# ─────────────────────────────────────────────
# CLIENT / TENANT
# ─────────────────────────────────────────────

def get_client_by_number(whatsapp_number: str) -> Optional[dict]:
    """Look up the SaaS client by their WhatsApp number."""
    db = get_db()
    # Normalize: strip whatsapp: prefix if present
    number = whatsapp_number.replace("whatsapp:", "")
    try:
        res = db.table("clients").select("*") \
            .eq("whatsapp_number", number) \
            .eq("is_active", True) \
            .single().execute()
        return res.data
    except Exception:
        return None


def upsert_client(name: str, whatsapp_number: str, business_name: str = "") -> dict:
    """Create or update a client record."""
    db = get_db()
    number = whatsapp_number.replace("whatsapp:", "")
    res = db.table("clients").upsert({
        "name": name,
        "whatsapp_number": number,
        "business_name": business_name,
        "is_active": True,
    }, on_conflict="whatsapp_number").execute()
    return res.data[0]


# ─────────────────────────────────────────────
# CUSTOMERS
# ─────────────────────────────────────────────

def get_or_create_customer(client_id: str, customer_name: str) -> dict:
    """Fetch or create a customer for the given client."""
    db = get_db()
    name_lower = customer_name.strip().title()
    try:
        res = db.table("customers").select("*") \
            .eq("client_id", client_id) \
            .ilike("name", name_lower) \
            .single().execute()
        return res.data
    except Exception:
        pass
    # Create new
    res = db.table("customers").insert({
        "client_id": client_id,
        "name": name_lower,
        "total_due": 0,
    }).execute()
    return res.data[0]


def search_customers(client_id: str, query: str) -> list[dict]:
    """Auto-suggest customers by partial name match."""
    db = get_db()
    res = db.table("customers").select("id, name, total_due") \
        .eq("client_id", client_id) \
        .ilike("name", f"%{query}%") \
        .limit(5).execute()
    return res.data or []


def get_all_customers(client_id: str) -> list[dict]:
    """All customers for dashboard."""
    db = get_db()
    res = db.table("customers").select("*") \
        .eq("client_id", client_id) \
        .order("name").execute()
    return res.data or []


# ─────────────────────────────────────────────
# RECORDS
# ─────────────────────────────────────────────

def get_previous_due(client_id: str, customer_name: str) -> float:
    """Get the most recent updated_due for a customer."""
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("records").select("updated_due") \
            .eq("client_id", client_id) \
            .ilike("customer_name", name) \
            .order("date", desc=True) \
            .order("created_at", desc=True) \
            .limit(1).single().execute()
        return float(res.data["updated_due"] or 0)
    except Exception:
        return 0.0


def add_record(client_id: str, customer_name: str, items: list[dict],
               current_total: float, payment: float,
               record_date: Optional[date] = None,
               notes: str = "") -> dict:
    """Insert a new ledger record and update customer total."""
    db = get_db()
    name = customer_name.strip().title()

    # Ensure customer exists
    customer = get_or_create_customer(client_id, name)

    previous_due = get_previous_due(client_id, name)
    updated_due = max(0.0, previous_due + current_total - payment)

    payload = {
        "client_id": client_id,
        "customer_id": customer["id"],
        "customer_name": name,
        "date": str(record_date or date.today()),
        "items": items,
        "current_total": current_total,
        "previous_due": previous_due,
        "payment": payment,
        "updated_due": updated_due,
        "notes": notes,
    }

    res = db.table("records").insert(payload).execute()
    record = res.data[0]

    # Update customer summary
    db.table("customers").update({
        "total_due": updated_due,
        "last_transaction_at": datetime.utcnow().isoformat(),
    }).eq("id", customer["id"]).execute()

    return record


def get_history(client_id: str, customer_name: str, limit: int = 10) -> list[dict]:
    """Last N records for a customer."""
    db = get_db()
    name = customer_name.strip().title()
    res = db.table("records").select("*") \
        .eq("client_id", client_id) \
        .ilike("customer_name", name) \
        .order("date", desc=True) \
        .order("created_at", desc=True) \
        .limit(limit).execute()
    return res.data or []


def get_latest_due(client_id: str, customer_name: str) -> Optional[float]:
    """Return current outstanding due for a customer, or None if not found."""
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("customers").select("total_due") \
            .eq("client_id", client_id) \
            .ilike("name", name) \
            .single().execute()
        return float(res.data["total_due"] or 0)
    except Exception:
        return None


def update_record(record_id: str, client_id: str,
                  items: Optional[list] = None,
                  current_total: Optional[float] = None,
                  payment: Optional[float] = None,
                  notes: Optional[str] = None) -> dict:
    """Update an existing record, then cascade-recalculate from that date."""
    db = get_db()

    # Fetch existing record
    existing = db.table("records").select("*") \
        .eq("id", record_id).eq("client_id", client_id) \
        .single().execute().data

    if not existing:
        raise ValueError("Record not found")

    patch = {}
    if items is not None:
        patch["items"] = items
        patch["current_total"] = sum(i.get("amount", 0) for i in items)
    if current_total is not None:
        patch["current_total"] = current_total
    if payment is not None:
        patch["payment"] = payment
    if notes is not None:
        patch["notes"] = notes

    db.table("records").update(patch).eq("id", record_id).execute()

    # Cascade recalculate
    db.rpc("recalculate_customer_dues", {
        "p_client_id": client_id,
        "p_customer_name": existing["customer_name"],
        "p_from_date": existing["date"],
    }).execute()

    return db.table("records").select("*").eq("id", record_id).single().execute().data


def delete_record(record_id: str, client_id: str) -> bool:
    """Delete a record and cascade-recalculate."""
    db = get_db()
    existing = db.table("records").select("customer_name, date") \
        .eq("id", record_id).eq("client_id", client_id) \
        .single().execute().data

    if not existing:
        return False

    db.table("records").delete().eq("id", record_id).execute()

    db.rpc("recalculate_customer_dues", {
        "p_client_id": client_id,
        "p_customer_name": existing["customer_name"],
        "p_from_date": existing["date"],
    }).execute()

    return True


def delete_last_record(client_id: str, customer_name: str) -> bool:
    """Undo — delete the most recent record for a customer."""
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("records").select("id, date") \
            .eq("client_id", client_id) \
            .ilike("customer_name", name) \
            .order("date", desc=True) \
            .order("created_at", desc=True) \
            .limit(1).single().execute()
        return delete_record(res.data["id"], client_id)
    except Exception:
        return False


def get_monthly_summary(client_id: str, year: int, month: int) -> list[dict]:
    """Aggregate per-customer totals for a given month."""
    db = get_db()
    start = f"{year}-{month:02d}-01"
    # Last day of month
    import calendar
    _, last_day = calendar.monthrange(year, month)
    end = f"{year}-{month:02d}-{last_day}"

    res = db.table("records").select(
        "customer_name, current_total, payment, updated_due"
    ).eq("client_id", client_id) \
     .gte("date", start).lte("date", end) \
     .execute()

    # Aggregate in Python
    summary: dict[str, dict] = {}
    for r in (res.data or []):
        cn = r["customer_name"]
        if cn not in summary:
            summary[cn] = {"customer_name": cn, "total_sales": 0, "total_payments": 0}
        summary[cn]["total_sales"]    += float(r["current_total"] or 0)
        summary[cn]["total_payments"] += float(r["payment"] or 0)

    return list(summary.values())


def get_overdue_customers(client_id: str, min_due: float = 1.0) -> list[dict]:
    """All customers with outstanding due >= min_due."""
    db = get_db()
    res = db.table("customers").select("name, phone, total_due, last_transaction_at") \
        .eq("client_id", client_id) \
        .gte("total_due", min_due) \
        .order("total_due", desc=True) \
        .execute()
    return res.data or []
