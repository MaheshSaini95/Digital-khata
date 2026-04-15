"""
services/database.py - Supabase client wrapper
Fixed for supabase>=2.28.3 — .single() removed (causes 406 errors)
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
    global _client
    if _client is None:
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    return _client


def get_client_by_number(whatsapp_number: str) -> Optional[dict]:
    """
    Look up client by primary OR alternative WhatsApp number.
    Supports multiple numbers per shop account.
    """
    db = get_db()
    number = whatsapp_number.replace("whatsapp:", "")
    try:
        # Check primary number
        res = db.table("clients").select("*") \
            .eq("whatsapp_number", number) \
            .eq("is_active", True) \
            .limit(1).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]

        # Check alt_numbers array (secondary numbers)
        try:
            res2 = db.table("clients").select("*") \
                .eq("is_active", True) \
                .contains("alt_numbers", [number]) \
                .limit(1).execute()
            if res2.data and len(res2.data) > 0:
                logger.info(f"Client found via alt number: {number}")
                return res2.data[0]
        except Exception:
            pass  # alt_numbers column may not exist yet

        return None
    except Exception as e:
        logger.error(f"get_client_by_number error: {e}")
        return None


def upsert_client(name: str, whatsapp_number: str, business_name: str = "") -> dict:
    db = get_db()
    number = whatsapp_number.replace("whatsapp:", "")
    res = db.table("clients").upsert({
        "name": name,
        "whatsapp_number": number,
        "business_name": business_name,
        "is_active": True,
    }, on_conflict="whatsapp_number").execute()
    return res.data[0]


def get_or_create_customer(client_id: str, customer_name: str) -> dict:
    db = get_db()
    name_lower = customer_name.strip().title()
    try:
        res = db.table("customers").select("*") \
            .eq("client_id", client_id) \
            .ilike("name", name_lower) \
            .limit(1).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception:
        pass
    res = db.table("customers").insert({
        "client_id": client_id,
        "name": name_lower,
        "total_due": 0,
    }).execute()
    return res.data[0]


def search_customers(client_id: str, query: str) -> list[dict]:
    db = get_db()
    res = db.table("customers").select("id, name, total_due") \
        .eq("client_id", client_id) \
        .ilike("name", f"%{query}%") \
        .limit(5).execute()
    return res.data or []


def get_all_customers(client_id: str) -> list[dict]:
    db = get_db()
    res = db.table("customers").select("*") \
        .eq("client_id", client_id) \
        .order("name").execute()
    return res.data or []


def get_previous_due(client_id: str, customer_name: str) -> float:
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("records").select("updated_due") \
            .eq("client_id", client_id) \
            .ilike("customer_name", name) \
            .order("date", desc=True) \
            .order("created_at", desc=True) \
            .limit(1).execute()
        if res.data and len(res.data) > 0:
            return float(res.data[0]["updated_due"] or 0)
        return 0.0
    except Exception as e:
        logger.error(f"get_previous_due error: {e}")
        return 0.0


def add_record(client_id: str, customer_name: str, items: list,
               current_total: float, payment: float,
               record_date=None, notes: str = "") -> dict:
    db = get_db()
    name = customer_name.strip().title()
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

    db.table("customers").update({
        "total_due": updated_due,
        "last_transaction_at": datetime.utcnow().isoformat(),
    }).eq("id", customer["id"]).execute()

    return record


def get_history(client_id: str, customer_name: str, limit: int = 10) -> list[dict]:
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
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("customers").select("total_due") \
            .eq("client_id", client_id) \
            .ilike("name", name) \
            .limit(1).execute()
        if res.data and len(res.data) > 0:
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

    try:
        db.rpc("recalculate_customer_dues", {
            "p_client_id": client_id,
            "p_customer_name": existing["customer_name"],
            "p_from_date": existing["date"],
        }).execute()
    except Exception as e:
        logger.error(f"Recalculate error: {e}")

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

    try:
        db.rpc("recalculate_customer_dues", {
            "p_client_id": client_id,
            "p_customer_name": existing["customer_name"],
            "p_from_date": existing["date"],
        }).execute()
    except Exception as e:
        logger.error(f"Recalculate error: {e}")

    return True


def delete_last_record(client_id: str, customer_name: str) -> bool:
    db = get_db()
    name = customer_name.strip().title()
    try:
        res = db.table("records").select("id, date") \
            .eq("client_id", client_id) \
            .ilike("customer_name", name) \
            .order("date", desc=True) \
            .order("created_at", desc=True) \
            .limit(1).execute()
        if res.data:
            return delete_record(res.data[0]["id"], client_id)
        return False
    except Exception as e:
        logger.error(f"delete_last_record error: {e}")
        return False


def get_monthly_summary(client_id: str, year: int, month: int) -> list[dict]:
    db = get_db()
    import calendar
    _, last_day = calendar.monthrange(year, month)
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day}"

    res = db.table("records").select(
        "customer_name, current_total, payment, updated_due"
    ).eq("client_id", client_id) \
     .gte("date", start).lte("date", end).execute()

    summary: dict[str, dict] = {}
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
        .eq("client_id", client_id) \
        .gte("total_due", min_due) \
        .order("total_due", desc=True).execute()
    return res.data or []
