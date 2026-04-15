"""
services/whatsapp_handler.py - Core FSM message processor
Fixed: quick entry no longer hijacks FSM mid-flow
"""
from __future__ import annotations
import logging
from datetime import date, datetime
from typing import Optional

from services import database as db
from services import session as sess
from utils.calculator import parse_voice_message, parse_items_text, eval_amount, format_items_list

logger = logging.getLogger(__name__)


MENU_TEXT = """📒 *Digital Khata* — Main Menu

Reply with a number:

1️⃣  *Add Entry*
2️⃣  *Payment*
3️⃣  *History*
4️⃣  *Due*
5️⃣  *Update*
6️⃣  *Delete / Undo*

📌 *Quick commands:*
• `history Rahul`
• `due Rahul`
• `undo Rahul`"""

HELP_TEXT = """ℹ️ *Help*

*Add entry:*
_Rahul milk 20 bread 15 payment 10_

*Check due:*  _due Rahul_
*History:*    _history Rahul_
*Undo:*       _undo Rahul_
*Delete:*     _delete Rahul 2024-01-15_

Reply *menu* anytime to restart."""


# ─────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────

def handle_message(from_number: str, body: str, media_url: Optional[str] = None) -> str:
    # Resolve client/tenant
    client = db.get_client_by_number(from_number)
    if not client:
        return _handle_unregistered(from_number)

    client_id = client["id"]
    session = sess.get_session(from_number)
    sess.set_session(from_number, client_id=client_id)

    # Voice processing
    if media_url:
        try:
            from services.voice import process_voice_message
            body = process_voice_message(media_url)
            logger.info(f"Voice→text: {body!r}")
        except Exception as e:
            logger.error(f"Voice processing failed: {e}")
            return "❌ Could not process voice. Please type your entry."

    body = (body or "").strip()
    lower = body.lower()

    # ── Global shortcuts — always work, reset FSM ─────────
    if lower in ("hi", "hello", "menu", "start", "0", "main menu"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return MENU_TEXT

    if lower in ("help", "?"):
        return HELP_TEXT

    # ── One-shot commands — work from any state ────────────
    if lower.startswith(("history ", "hist ")):
        name = body.split(" ", 1)[1].strip()
        return _cmd_history(client_id, name)

    if lower.startswith(("due ", "baaki ")):
        name = body.split(" ", 1)[1].strip()
        return _cmd_due(client_id, name)

    if lower.startswith("undo "):
        name = body.split(" ", 1)[1].strip()
        return _cmd_undo(client_id, name)

    if lower.startswith("delete "):
        parts = body.split()
        if len(parts) >= 3:
            return _cmd_delete_by_date(client_id, parts[1], parts[2])
        return "❌ Usage: _delete CustomerName YYYY-MM-DD_"

    # ── Get current FSM state ─────────────────────────────
    state = session.get("state", "idle")

    # ── IDLE — show menu or detect quick entry ────────────
    if state == "idle":
        # Only allow quick entry from IDLE state (not mid-flow)
        if _looks_like_quick_entry(lower):
            return _handle_quick_entry(client_id, from_number, body)
        return _handle_idle_input(client_id, from_number, lower)

    # ── ADD ENTRY flow ─────────────────────────────────────
    if state == "add_ask_name":
        return _add_ask_name(client_id, from_number, body)

    if state == "add_ask_items":
        # FIX: do NOT run quick-entry parser here — just parse items directly
        return _add_ask_items(client_id, from_number, body)

    if state == "add_ask_payment":
        return _add_ask_payment(client_id, from_number, body)

    if state == "add_ask_phone":
        return _add_ask_phone(client_id, from_number, body)

    if state == "add_confirm":
        return _add_confirm(client_id, from_number, lower)

    # ── PAYMENT flow ───────────────────────────────────────
    if state == "pay_ask_name":
        return _pay_ask_name(client_id, from_number, body)

    if state == "pay_ask_amount":
        return _pay_ask_amount(client_id, from_number, body)

    if state == "pay_confirm":
        return _pay_confirm(client_id, from_number, lower)

    # ── UPDATE flow ────────────────────────────────────────
    if state == "upd_ask_name":
        return _upd_ask_name(client_id, from_number, body)

    if state == "upd_show_records":
        return _upd_show_records(client_id, from_number, body)

    if state == "upd_ask_field":
        return _upd_ask_field(client_id, from_number, body)

    if state == "upd_ask_value":
        return _upd_ask_value(client_id, from_number, body)

    # Fallback
    sess.clear_session(from_number)
    sess.set_session(from_number, client_id=client_id)
    return MENU_TEXT


# ─────────────────────────────────────────────────────────
# UNREGISTERED
# ─────────────────────────────────────────────────────────

def _handle_unregistered(from_number: str) -> str:
    return (
        "👋 Welcome to *Digital Khata*!\n\n"
        "Your number is not registered as a shop account.\n"
        "Please contact support Team to get started.\n"
        "Please contact Us - 9509200933."
    )


# ─────────────────────────────────────────────────────────
# IDLE MENU ROUTING
# ─────────────────────────────────────────────────────────

def _handle_idle_input(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("1", "add", "add entry", "entry"):
        sess.set_session(from_number, state="add_ask_name")
        return "📝 *Add Entry*\n\nEnter *customer name*:"

    if lower in ("2", "payment", "pay"):
        sess.set_session(from_number, state="pay_ask_name")
        return "💰 *Record Payment*\n\nEnter *customer name*:"

    if lower in ("3", "history", "hist"):
        return "📜 Type: *history <name>*\nExample: _history Rahul_"

    if lower in ("4", "due", "check due"):
        return "💸 Type: *due <name>*\nExample: _due Rahul_"

    if lower in ("5", "update"):
        sess.set_session(from_number, state="upd_ask_name")
        return "✏️ *Update Entry*\n\nEnter *customer name*:"

    if lower in ("6", "delete", "undo"):
        return (
            "🗑️ *Delete / Undo*\n\n"
            "• Last entry: _undo Rahul_\n"
            "• By date: _delete Rahul 2024-01-15_"
        )

    return MENU_TEXT


# ─────────────────────────────────────────────────────────
# QUICK ENTRY (only from IDLE — voice shorthand)
# ─────────────────────────────────────────────────────────

def _looks_like_quick_entry(lower: str) -> bool:
    """
    Only trigger quick entry if:
    - At least 3 tokens
    - First token looks like a name (no digits)
    - At least one later token has digits
    - Contains item-like pattern (word followed by number)
    """
    tokens = lower.split()
    if len(tokens) < 3:
        return False
    # First token must be a pure name (no digits)
    if any(c.isdigit() for c in tokens[0]):
        return False
    # Must have at least one number in remaining tokens
    has_number = any(any(c.isdigit() for c in t) for t in tokens[1:])
    return has_number


def _handle_quick_entry(client_id: str, from_number: str, body: str) -> str:
    parsed = parse_voice_message(body)
    if not parsed["customer_name"] or not parsed["items"]:
        return MENU_TEXT

    prev_due = db.get_previous_due(client_id, parsed["customer_name"])
    updated_due = max(0.0, prev_due + parsed["current_total"] - parsed["payment"])

    sess.set_session(
        from_number,
        state="add_confirm",
        customer_name=parsed["customer_name"],
        items=parsed["items"],
        current_total=parsed["current_total"],
        payment=parsed["payment"],
    )

    return _build_confirm_text(
        parsed["customer_name"],
        parsed["items"],
        parsed["current_total"],
        parsed["payment"],
        prev_due,
        updated_due,
    )


# ─────────────────────────────────────────────────────────
# ADD ENTRY FLOW
# ─────────────────────────────────────────────────────────

def _add_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    if not name:
        return "Please enter a valid customer name:"

    matches = db.search_customers(client_id, name)
    sess.set_session(from_number, state="add_ask_items", customer_name=name)

    suggestion = ""
    if matches and matches[0]["name"].lower() != name.lower():
        top = matches[0]
        suggestion = f"\n💡 Similar: *{top['name']}* (due: ₹{top['total_due']:.0f})\n"

    return (
        f"👤 Customer: *{name}*{suggestion}\n\n"
        f"Enter *items and amounts*:\n"
        f"Examples:\n"
        f"• _milk 20 bread 15_\n"
        f"• _rice 50 dal 30 oil 80_\n"
        f"• _loki 10 aloo 20_"
    )


def _add_ask_items(client_id: str, from_number: str, body: str) -> str:
    """
    FIX: Parse the entire body as items — do NOT extract customer name here.
    The customer name was already captured in the previous step.
    """
    items, total = parse_items_text(body)

    if not items:
        return (
            "❌ Could not parse items. Please try again.\n\n"
            "Format: _item1 amount item2 amount_\n"
            "Example: _milk 20 bread 15 rice 50_"
        )

    sess.set_session(from_number, state="add_ask_payment", items=items, current_total=total)

    items_display = "\n".join(f"  • {it['name']}: ₹{it['amount']:.0f}" for it in items)
    return (
        f"🛒 *Items recorded:*\n{items_display}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"*Total: ₹{total:.0f}*\n\n"
        f"Enter *payment received* (or 0 if none):"
    )


def _add_ask_payment(client_id: str, from_number: str, body: str) -> str:
    payment = eval_amount(body)
    session = sess.get_session(from_number)
    customer_name = session["customer_name"]
    items = session["items"]
    current_total = session["current_total"]

    prev_due = db.get_previous_due(client_id, customer_name)
    updated_due = max(0.0, prev_due + current_total - payment)

    sess.set_session(from_number, state="add_confirm", payment=payment,
                     _prev_due=prev_due, _updated_due=updated_due)

    # Check if customer already has a phone number
    from services.database import get_db
    db_client = get_db()
    cust = db_client.table("customers").select("phone") \
        .eq("client_id", client_id) \
        .ilike("name", customer_name.title()) \
        .limit(1).execute()

    has_phone = cust.data and cust.data[0].get("phone")

    if not has_phone:
        # Ask for phone number first
        sess.set_session(from_number, state="add_ask_phone", payment=payment,
                         _prev_due=prev_due, _updated_due=updated_due)
        return (
            f"{_build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)}\n\n"
            f"📱 Enter *customer phone number* to send bill\n"
            f"(or type *skip* to skip)"
        )

    return _build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)


def _add_ask_phone(client_id: str, from_number: str, body: str) -> str:
    """Save customer phone number then show confirm."""
    session = sess.get_session(from_number)
    customer_name = session["customer_name"]
    items = session["items"]
    current_total = session["current_total"]
    payment = session["payment"]
    prev_due = session.get("_prev_due", 0)
    updated_due = session.get("_updated_due", 0)

    raw_input = body.strip()
    phone = raw_input

    if phone.lower() != "skip" and phone:
        # Normalize phone number
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")  # Default India country code

        # Save phone to customer record
        try:
            from services.database import get_db
            db_client = get_db()
            # First get or create customer, then update phone
            existing = db_client.table("customers").select("id") \
                .eq("client_id", client_id) \
                .ilike("name", customer_name.title()) \
                .limit(1).execute()
            if existing.data:
                db_client.table("customers").update({"phone": phone}) \
                    .eq("id", existing.data[0]["id"]).execute()
                logger.info(f"Updated phone {phone} for {customer_name}")
            else:
                db_client.table("customers").insert({
                    "client_id": client_id,
                    "name": customer_name.title(),
                    "phone": phone,
                    "total_due": 0,
                }).execute()
                logger.info(f"Created customer with phone {phone} for {customer_name}")
        except Exception as e:
            logger.error(f"Could not save phone: {e}")

    # Store phone in session for immediate use in bill sending
    sess.set_session(from_number, state="add_confirm", _customer_phone=phone if phone.lower() != "skip" else "")
    return _build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)


def _add_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ha", "ok", "save", "1"):
        session = sess.get_session(from_number)
        record = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=session["items"],
            current_total=session["current_total"],
            payment=session["payment"],
        )
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)

        # ── Generate and send bill ──────────────────────────
        try:
            from services.database import get_db
            from utils.bill_generator import generate_and_send_bill

            db_client = get_db()
            client_rec = db_client.table("clients").select("name, business_name") \
                .eq("id", client_id).limit(1).execute()
            business_name = "My Store"
            if client_rec.data:
                business_name = client_rec.data[0].get("business_name") or client_rec.data[0].get("name", "My Store")

            # Get customer phone - first check session (just entered), then DB
            session_phone = session.get("_customer_phone", "")
            if session_phone:
                customer_phone = session_phone
                logger.info(f"Using phone from session: {customer_phone}")
            else:
                # Fallback: check DB
                cust = db_client.table("customers").select("phone") \
                    .eq("client_id", client_id) \
                    .ilike("name", record["customer_name"]) \
                    .limit(1).execute()
                customer_phone = cust.data[0].get("phone", "") if cust.data else ""
                logger.info(f"Using phone from DB: {customer_phone}")

            import threading
            def send_bill():
                generate_and_send_bill(
                    business_name=business_name,
                    customer_name=record["customer_name"],
                    customer_phone=customer_phone,
                    items=record.get("items") or [],
                    current_total=float(record["current_total"]),
                    payment=float(record["payment"]),
                    previous_due=float(record["previous_due"]),
                    updated_due=float(record["updated_due"]),
                    record_date=record["date"],
                    sender_number=from_number,
                )
            threading.Thread(target=send_bill, daemon=True).start()
        except Exception as e:
            logger.error(f"Bill generation error: {e}")

        return (
            f"✅ *Entry saved!*\n\n"
            f"👤 {record['customer_name']}\n"
            f"📅 {record['date']}\n"
            f"💰 Outstanding Due: ₹{record['updated_due']:.0f}\n\n"
            f"🧾 Bill is being sent...\n"
            "Reply *menu* for more options."
        )

    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Entry cancelled.\n\nReply *menu* to start over."

    return "Please reply *yes* to save or *no* to cancel."


def _build_confirm_text(name, items, current_total, payment, prev_due, updated_due) -> str:
    lines = []
    for it in (items or []):
        qty  = it.get("qty", 1)
        rate = it.get("rate", it.get("amount", 0))
        amt  = float(it.get("amount", 0))
        nm   = it.get("name", "")
        if qty > 1 and rate != amt:
            lines.append(f"  • {nm}: {qty:.0f} × {rate:.0f} = ₹{amt:.0f}")
        else:
            lines.append(f"  • {nm}: ₹{amt:.0f}")
    items_display = "\n".join(lines) if lines else "  (Payment only)"
    return (
        f"📋 *Confirm Entry*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 Customer: *{name}*\n"
        f"🛒 Items:\n{items_display}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 Bill Total: ₹{current_total:.0f}\n"
        f"💰 Payment: ₹{payment:.0f}\n"
        f"📊 Previous Due: ₹{prev_due:.0f}\n"
        f"🔴 New Due: *₹{updated_due:.0f}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Reply *yes* to save or *no* to cancel."
    )


# ─────────────────────────────────────────────────────────
# PAYMENT FLOW
# ─────────────────────────────────────────────────────────

def _pay_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    due = db.get_latest_due(client_id, name)
    if due is None:
        return f"❌ Customer *{name}* not found.\nEnter correct name or *menu* to cancel:"

    sess.set_session(from_number, state="pay_ask_amount", customer_name=name)
    return f"💰 *{name}* — Current due: *₹{due:.0f}*\n\nEnter *payment amount*:"


def _pay_ask_amount(client_id: str, from_number: str, body: str) -> str:
    amount = eval_amount(body)
    if amount <= 0:
        return "Please enter a valid payment amount (numbers only):"

    session = sess.get_session(from_number)
    name = session["customer_name"]
    prev_due = db.get_previous_due(client_id, name)
    updated_due = max(0.0, prev_due - amount)

    sess.set_session(from_number, state="pay_confirm", payment=amount)

    return (
        f"💰 *Payment Confirmation*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 Customer: *{name}*\n"
        f"📊 Previous Due: ₹{prev_due:.0f}\n"
        f"✅ Payment: ₹{amount:.0f}\n"
        f"🟢 New Due: *₹{updated_due:.0f}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Reply *yes* to confirm or *no* to cancel."
    )


def _pay_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ok", "1"):
        session = sess.get_session(from_number)
        record = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=[],
            current_total=0.0,
            payment=session["payment"],
            notes="Payment entry",
        )
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return (
            f"✅ *Payment recorded!*\n\n"
            f"👤 {record['customer_name']}\n"
            f"💰 Paid: ₹{record['payment']:.0f}\n"
            f"🟢 Remaining Due: ₹{record['updated_due']:.0f}\n\n"
            "Reply *menu* for more options."
        )

    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Payment cancelled.\n\nReply *menu* to start over."

    return "Please reply *yes* to confirm or *no* to cancel."


# ─────────────────────────────────────────────────────────
# HISTORY / DUE / UNDO / DELETE
# ─────────────────────────────────────────────────────────

def _cmd_history(client_id: str, customer_name: str) -> str:
    records = db.get_history(client_id, customer_name, limit=7)
    if not records:
        return f"📭 No records found for *{customer_name.title()}*."

    lines = [f"📜 *History: {customer_name.title()}*\n"]
    for r in records:
        items_str = ", ".join(
            f"{it['name']} ₹{it['amount']:.0f}" for it in (r.get("items") or [])
        ) or "Payment only"
        lines.append(
            f"📅 *{r['date']}*\n"
            f"  Items: {items_str}\n"
            f"  Paid: ₹{r['payment']:.0f} | Due: ₹{r['updated_due']:.0f}\n"
        )

    lines.append("Reply *menu* for more options.")
    return "\n".join(lines)


def _cmd_due(client_id: str, customer_name: str) -> str:
    due = db.get_latest_due(client_id, customer_name)
    if due is None:
        return f"❌ Customer *{customer_name.title()}* not found."
    emoji = "🟢" if due == 0 else "🔴"
    return (
        f"{emoji} *{customer_name.title()}*\n"
        f"Outstanding due: *₹{due:.0f}*"
    )


def _cmd_undo(client_id: str, customer_name: str) -> str:
    ok = db.delete_last_record(client_id, customer_name)
    if ok:
        due = db.get_latest_due(client_id, customer_name) or 0
        return (
            f"✅ Last entry for *{customer_name.title()}* undone.\n"
            f"Updated due: ₹{due:.0f}"
        )
    return f"❌ No records found for *{customer_name.title()}*."


def _cmd_delete_by_date(client_id: str, customer_name: str, date_str: str) -> str:
    from services.database import get_db
    db_client = get_db()
    try:
        res = db_client.table("records").select("id") \
            .eq("client_id", client_id) \
            .ilike("customer_name", customer_name.title()) \
            .eq("date", date_str) \
            .limit(1).execute()
        if not res.data:
            return f"❌ No record found for *{customer_name}* on *{date_str}*."
        db.delete_record(res.data[0]["id"], client_id)
        return f"✅ Record deleted for *{customer_name.title()}* on *{date_str}*."
    except Exception as e:
        logger.error(f"Delete by date error: {e}")
        return "❌ Error deleting record. Check date format: YYYY-MM-DD"


# ─────────────────────────────────────────────────────────
# UPDATE FLOW
# ─────────────────────────────────────────────────────────

def _upd_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    records = db.get_history(client_id, name, limit=5)
    if not records:
        return f"❌ No records found for *{name}*.\nEnter another name or *menu*:"

    lines = [f"✏️ *Recent entries for {name}:*\n"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. 📅 {r['date']} — ₹{r['current_total']:.0f} (paid ₹{r['payment']:.0f})")

    sess.set_session(
        from_number,
        state="upd_show_records",
        customer_name=name,
        _records=[r["id"] for r in records],
    )

    lines.append("\nReply with *number* (1-5) to select entry:")
    return "\n".join(lines)


def _upd_show_records(client_id: str, from_number: str, body: str) -> str:
    try:
        idx = int(body.strip()) - 1
    except ValueError:
        return "Please reply with a number (1-5):"

    session = sess.get_session(from_number)
    record_ids = session.get("_records", [])
    if idx < 0 or idx >= len(record_ids):
        return "Invalid selection. Reply with a number shown above:"

    sess.set_session(from_number, state="upd_ask_field", record_id=record_ids[idx])
    return (
        "What would you like to update?\n\n"
        "1. *Items / Total*\n"
        "2. *Payment*\n\n"
        "Reply 1 or 2:"
    )


def _upd_ask_field(client_id: str, from_number: str, body: str) -> str:
    lower = body.strip().lower()
    if lower in ("1", "items", "total"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="items")
        return "Enter new items:\nExample: _milk 20 bread 15_"
    if lower in ("2", "payment", "pay"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="payment")
        return "Enter new payment amount:"
    return "Please reply *1* for items or *2* for payment:"


def _upd_ask_value(client_id: str, from_number: str, body: str) -> str:
    session = sess.get_session(from_number)
    record_id = session.get("record_id")
    field = session.get("_upd_field")

    try:
        if field == "items":
            items, total = parse_items_text(body)
            db.update_record(record_id, client_id, items=items, current_total=total)
        elif field == "payment":
            amount = eval_amount(body)
            db.update_record(record_id, client_id, payment=amount)

        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "✅ Entry updated and dues recalculated!\n\nReply *menu* for more options."
    except Exception as e:
        logger.error(f"Update error: {e}")
        return "❌ Update failed. Please try again or reply *menu*."
