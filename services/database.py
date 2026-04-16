"""
services/database.py - Supabase client wrapper v2
- Multi-number client lookup
- Backdated entry support
- Enhanced customer/record operations
"""
from __future__ import annotations
import logging
import calendar
from datetime import date, datetime
from typing import Optional
from supabase import create_client, Client
from config import Config

logger = logging.getLogger(__name__)
_client: Optional[Client] = None


def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    return _client


# ─────────────────────────────────────────────────────────────
# CLIENT LOOKUP — supports multi-number
# ─────────────────────────────────────────────────────────────

def get_client_by_number(whatsapp_number: str) -> Optional[dict]:
    """
    Find client by any registered number (primary or secondary).
    Uses client_numbers table for fast lookup.
    """
    db = get_db()
    number = whatsapp_number.replace("whatsapp:", "").replace("+", "")
    # also try with + prefix
    number_plus = "+" + number

    for num in (number_plus, number):
        try:
            # Check client_numbers table first (supports multi-number)
            res = db.table("client_numbers").select("client_id") \
                .eq("number", num).limit(1).execute()

            if res.data:
                client_id = res.data[0]["client_id"]
                client_res = db.table("clients").select("*") \
                    .eq("id", client_id).eq("is_active", True) \
                    .limit(1).execute()
                if client_res.data:
                    return client_res.data[0]

            # Fallback: direct whatsapp_number match
            res2 = db.table("clients").select("*") \
                .eq("whatsapp_number", num) \
                .eq("is_active", True).limit(1).execute()
            if res2.data:
                return res2.data[0]

        except Exception as e:
            logger.error(f"get_client_by_number error for {num}: {e}")

    return None


def is_number_registered(number: str) -> bool:
    """Check if a number already has an account."""
    return get_client_by_number(number) is not None


def upsert_client(name: str, whatsapp_number: str,
                  business_name: str = "") -> dict:
    db = get_db()
    number = whatsapp_number.replace("whatsapp:", "")
    res = db.table("clients").upsert({
        "name": name,
        "whatsapp_number": number,
        "business_name": business_name,
        "is_active": True,
        "onboarding_step": "complete",
    }, on_conflict="whatsapp_number").execute()
    return res.data[0]


# ─────────────────────────────────────────────────────────────
# CUSTOMERS
# ─────────────────────────────────────────────────────────────

def get_or_create_customer(client_id: str, customer_name: str) -> dict:
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("customers").select("*") \
            .eq("client_id", client_id).ilike("name", name) \
            .limit(1).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    res = db.table("customers").insert({
        "client_id": client_id, "name": name, "total_due": 0,
    }).execute()
    return res.data[0]


def search_customers(client_id: str, query: str) -> list[dict]:
    db = get_db()
    res = db.table("customers").select("id, name, total_due") \
        .eq("client_id", client_id).ilike("name", f"%{query}%") \
        .limit(5).execute()
    return res.data or []


def get_all_customers(client_id: str) -> list[dict]:
    db = get_db()
    res = db.table("customers").select("*") \
        .eq("client_id", client_id).order("name").execute()
    return res.data or []


# ─────────────────────────────────────────────────────────────
# RECORDS — with backdating support
# ─────────────────────────────────────────────────────────────

def get_previous_due(client_id: str, customer_name: str,
                     before_date: Optional[date] = None) -> float:
    """
    Get the most recent updated_due for a customer.
    If before_date is given, returns due AS OF that date
    (for backdated entry support).
    """
    db = get_db()
    name = customer_name.strip().title()
    try:
        q = db.table("records").select("updated_due") \
            .eq("client_id", client_id).ilike("customer_name", name)

        if before_date:
            # Get last record strictly before the given date
            q = q.lt("date", str(before_date))

        res = q.order("date", desc=True) \
               .order("created_at", desc=True) \
               .limit(1).execute()

        if res.data:
            return float(res.data[0]["updated_due"] or 0)
        return 0.0
    except Exception as e:
        logger.error(f"get_previous_due error: {e}")
        return 0.0


def add_record(client_id: str, customer_name: str,
               items: list, current_total: float,
               payment: float, record_date: Optional[date] = None,
               notes: str = "") -> dict:
    """
    Insert a new ledger record.
    Supports backdated entries — recalculates cascade automatically.
    """
    db = get_db()
    name = customer_name.strip().title()
    customer = get_or_create_customer(client_id, name)

    entry_date = record_date or date.today()

    # Get previous due AS OF the entry date (for backdating)
    previous_due = get_previous_due(client_id, name, before_date=entry_date)
    updated_due  = max(0.0, previous_due + current_total - payment)

    payload = {
        "client_id":    client_id,
        "customer_id":  customer["id"],
        "customer_name": name,
        "date":         str(entry_date),
        "items":        items,
        "current_total": current_total,
        "previous_due": previous_due,
        "payment":      payment,
        "updated_due":  updated_due,
        "notes":        notes,
    }

    res = db.table("records").insert(payload).execute()
    record = res.data[0]

    # If backdated, cascade-recalculate all future records
    if record_date and record_date < date.today():
        logger.info(f"Backdated entry — recalculating cascade for {name} from {record_date}")
        _cascade_recalculate(client_id, name, str(record_date))
        # Re-fetch after cascade
        res2 = db.table("records").select("*") \
            .eq("id", record["id"]).limit(1).execute()
        if res2.data:
            record = res2.data[0]
    else:
        # Update customer summary
        db.table("customers").update({
            "total_due": updated_due,
            "last_transaction_at": datetime.utcnow().isoformat(),
        }).eq("id", customer["id"]).execute()

    return record


def _cascade_recalculate(client_id: str, customer_name: str,
                          from_date: str) -> None:
    """Recalculate all records from from_date onwards."""
    db = get_db()
    try:
        db.rpc("recalculate_customer_dues", {
            "p_client_id":    client_id,
            "p_customer_name": customer_name,
            "p_from_date":    from_date,
        }).execute()
    except Exception as e:
        logger.error(f"Cascade recalculate error: {e}")


def get_history(client_id: str, customer_name: str,
                limit: int = 10) -> list[dict]:
    db = get_db()
    name = customer_name.strip().title()
    res = db.table("records").select("*") \
        .eq("client_id", client_id).ilike("customer_name", name) \
        .order("date", desc=True).order("created_at", desc=True) \
        .limit(limit).execute()
    return res.data or []


def get_latest_due(client_id: str, customer_name: str) -> Optional[float]:
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("customers").select("total_due") \
            .eq("client_id", client_id).ilike("name", name) \
            .limit(1).execute()
        if res.data:
            return float(res.data[0]["total_due"] or 0)
        return None
    except Exception as e:
        logger.error(f"get_latest_due error: {e}")
        return None


def update_record(record_id: str, client_id: str,
                  items=None, current_total=None,
                  payment=None, notes=None) -> dict:
    db = get_db()
    res = db.table("records").select("*") \
        .eq("id", record_id).eq("client_id", client_id) \
        .limit(1).execute()
    if not res.data:
        raise ValueError("Record not found")

    existing = res.data[0]
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
    _cascade_recalculate(client_id, existing["customer_name"], existing["date"])

    res2 = db.table("records").select("*").eq("id", record_id).limit(1).execute()
    return res2.data[0]


def delete_record(record_id: str, client_id: str) -> bool:
    db = get_db()
    res = db.table("records").select("customer_name, date") \
        .eq("id", record_id).eq("client_id", client_id) \
        .limit(1).execute()
    if not res.data:
        return False
    existing = res.data[0]
    db.table("records").delete().eq("id", record_id).execute()
    _cascade_recalculate(client_id, existing["customer_name"], existing["date"])
    return True


def delete_last_record(client_id: str, customer_name: str) -> bool:
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("records").select("id, date") \
            .eq("client_id", client_id).ilike("customer_name", name) \
            .order("date", desc=True).order("created_at", desc=True) \
            .limit(1).execute()
        if res.data:
            return delete_record(res.data[0]["id"], client_id)
        return False
    except Exception as e:
        logger.error(f"delete_last_record error: {e}")
        return False


def get_monthly_summary(client_id: str, year: int, month: int) -> list[dict]:
    db = get_db()
    _, last_day = calendar.monthrange(year, month)
    start = f"{year}-{month:02d}-01"
    end   = f"{year}-{month:02d}-{last_day}"

    res = db.table("records").select(
        "customer_name, current_total, payment, updated_due"
    ).eq("client_id", client_id).gte("date", start).lte("date", end).execute()

    summary: dict = {}
    for r in (res.data or []):
        cn = r["customer_name"]
        if cn not in summary:
            summary[cn] = {"customer_name": cn, "total_sales": 0, "total_payments": 0}
        summary[cn]["total_sales"]    += float(r["current_total"] or 0)
        summary[cn]["total_payments"] += float(r["payment"] or 0)
    return list(summary.values())


def get_overdue_customers(client_id: str, min_due: float = 1.0) -> list[dict]:
    db = get_db()
    res = db.table("customers").select("name, phone, total_due, last_transaction_at") \
        .eq("client_id", client_id).gte("total_due", min_due) \
        .order("total_due", desc=True).execute()
    return res.data or []
