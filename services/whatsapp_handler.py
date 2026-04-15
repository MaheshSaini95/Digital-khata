"""
services/whatsapp_handler.py — Digital Khata
Updated: Multi-member access + Hinglish registration + Team management
Fixed: quick entry no longer hijacks FSM mid-flow
"""
from __future__ import annotations
import logging
import threading
from datetime import date, datetime
from typing import Optional

from services import database as db
from services import session as sess
from utils.calculator import parse_voice_message, parse_items_text, eval_amount, format_items_list

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────

BRAND   = "📒 *Digital Khata*"
DIV     = "━━━━━━━━━━━━━━━"
DIV_SM  = "─────────────────"

MENU_TEXT = (
    f"{BRAND} — Main Menu\n\n"
    "1️⃣  Naya Entry\n"
    "2️⃣  Payment Lena\n"
    "3️⃣  History Dekhna\n"
    "4️⃣  Baaki (Due)\n"
    "5️⃣  Entry Update\n"
    "6️⃣  Delete / Undo\n"
    "7️⃣  Team Manage\n\n"
    f"{DIV}\n"
    "📌 *Quick commands:*\n"
    "  • `history Rahul`\n"
    "  • `due Rahul`\n"
    "  • `undo Rahul`\n\n"
    "💡 _Direct entry:_ `Rahul milk 20 bread 15`"
)

HELP_TEXT = (
    f"{BRAND} — Help\n"
    f"{DIV}\n\n"
    "📝 *Entry:*  _Rahul milk 20 bread 15 paid 50_\n"
    "💰 *Payment:*  Menu → 2 → naam → amount\n"
    "📜 *History:*  `history Rahul`\n"
    "💸 *Due:*  `due Rahul`\n"
    "↩️ *Undo:*  `undo Rahul`\n"
    "🗑️ *Delete:*  `delete Rahul 2024-01-15`\n"
    "👥 *Member add:*  `add member 919XXXXXXXXX`\n\n"
    "_Kabhi bhi_ *menu* _likh kar restart karen_"
)


# ─────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────

def handle_message(from_number: str, body: str, media_url: Optional[str] = None) -> str:
    client = db.get_client_by_number(from_number)
    if not client:
        return _handle_unregistered(from_number, body)

    client_id = client["id"]
    session   = sess.get_session(from_number)
    sess.set_session(from_number, client_id=client_id)

    # Voice processing
    if media_url:
        try:
            from services.voice import process_voice_message
            body = process_voice_message(media_url)
            logger.info(f"Voice→text: {body!r}")
        except Exception as e:
            logger.error(f"Voice processing failed: {e}")
            return "❌ Voice samajh nahi aaya. Please type karein."

    body  = (body or "").strip()
    lower = body.lower()

    # ── Global shortcuts ──────────────────────────────────
    if lower in ("hi", "hello", "menu", "start", "0", "main menu", "helo"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return f"👋 Welcome back!\n\n{MENU_TEXT}"

    if lower in ("help", "?", "madad"):
        return HELP_TEXT

    # ── One-shot commands ─────────────────────────────────
    if lower.startswith(("history ", "hist ")):
        return _cmd_history(client_id, body.split(" ", 1)[1].strip())

    if lower.startswith(("due ", "baaki ")):
        return _cmd_due(client_id, body.split(" ", 1)[1].strip())

    if lower.startswith("undo "):
        return _cmd_undo(client_id, body.split(" ", 1)[1].strip())

    if lower.startswith("delete "):
        parts = body.split()
        if len(parts) >= 3:
            return _cmd_delete_by_date(client_id, parts[1], parts[2])
        return "❌ Format: `delete CustomerName YYYY-MM-DD`"

    # ── Team member shortcuts ─────────────────────────────
    if lower.startswith("add member"):
        parts = body.split(maxsplit=2)
        if len(parts) < 3:
            return (
                f"📱 *Member Add Karna*\n{DIV}\n"
                "Format: `add member 919XXXXXXXXX`\n"
                "_Country code ke saath (India: 91)_"
            )
        number = parts[2].strip().replace("+", "").replace(" ", "")
        return _cmd_add_member(client_id, number)

    if lower.startswith("remove member"):
        parts = body.split(maxsplit=2)
        if len(parts) < 3:
            return "Format: `remove member 919XXXXXXXXX`"
        return _cmd_remove_member(client_id, parts[2].strip().replace("+", "").replace(" ", ""))

    if lower in ("members", "team", "list members", "7", "team manage"):
        return _cmd_list_members(client_id)

    # ── FSM state dispatch ────────────────────────────────
    state = session.get("state", "idle")

    if state == "idle":
        if _looks_like_quick_entry(lower):
            return _handle_quick_entry(client_id, from_number, body)
        return _handle_idle_input(client_id, from_number, lower)

    if state == "add_ask_name":     return _add_ask_name(client_id, from_number, body)
    if state == "add_ask_items":    return _add_ask_items(client_id, from_number, body)
    if state == "add_ask_payment":  return _add_ask_payment(client_id, from_number, body)
    if state == "add_ask_phone":    return _add_ask_phone(client_id, from_number, body)
    if state == "add_confirm":      return _add_confirm(client_id, from_number, lower)

    if state == "pay_ask_name":     return _pay_ask_name(client_id, from_number, body)
    if state == "pay_ask_amount":   return _pay_ask_amount(client_id, from_number, body)
    if state == "pay_confirm":      return _pay_confirm(client_id, from_number, lower)

    if state == "upd_ask_name":     return _upd_ask_name(client_id, from_number, body)
    if state == "upd_show_records": return _upd_show_records(client_id, from_number, body)
    if state == "upd_ask_field":    return _upd_ask_field(client_id, from_number, body)
    if state == "upd_ask_value":    return _upd_ask_value(client_id, from_number, body)

    # Fallback
    sess.clear_session(from_number)
    sess.set_session(from_number, client_id=client_id)
    return MENU_TEXT


# ─────────────────────────────────────────────────────────
# REGISTRATION FLOW  (unregistered numbers)
# ─────────────────────────────────────────────────────────

def _handle_unregistered(from_number: str, body: str = "") -> str:
    session = sess.get_session(from_number)
    state   = session.get("state", "idle")

    if state == "reg_ask_name":
        return _reg_ask_name(from_number, body)
    if state == "reg_ask_business":
        return _reg_ask_business(from_number, body)
    if state == "reg_confirm":
        return _reg_confirm(from_number, body)

    # Fresh visitor — start registration
    sess.set_session(from_number, state="reg_ask_name")
    return (
        "👋 *Digital Khata mein Aapka Swagat Hai!*\n\n"
        "📲 WhatsApp pe apni dukaan ka hisaab rakhein — bilkul free!\n\n"
        "✅  Udhaar entry\n"
        "✅  Payment record\n"
        "✅  WhatsApp bill\n"
        "✅  Team access\n\n"
        f"{DIV}\n"
        "Shuru karne ke liye apna *pura naam* likhein:"
    )


def _reg_ask_name(from_number: str, body: str) -> str:
    name = body.strip().title()
    if not name or len(name) < 2:
        return "✏️ Apna *pura naam* likhein (kam se kam 2 letters):"
    sess.set_session(from_number, state="reg_ask_business", owner_name=name)
    return (
        f"👍 Namaste *{name}*!\n\n"
        "🏪 Apni *dukaan / business ka naam* likhein:\n"
        "_(Example: Sharma General Store, Raj Medical)_"
    )


def _reg_ask_business(from_number: str, body: str) -> str:
    business = body.strip()
    if not business or len(business) < 2:
        return "🏪 Apni *dukaan ka naam* likhein:"
    session    = sess.get_session(from_number)
    owner_name = session.get("owner_name", "")
    sess.set_session(from_number, state="reg_confirm",
                     owner_name=owner_name, business_name=business)
    return (
        f"{BRAND}\n"
        f"{DIV}\n"
        f"✅ *Registration Confirm Karein*\n"
        f"{DIV}\n"
        f"👤 Naam:      *{owner_name}*\n"
        f"🏪 Dukaan:    *{business}*\n"
        f"📱 WhatsApp:  *+{from_number}*\n"
        f"{DIV}\n"
        "Sahi hai? *yes* likhein\n"
        "Badlna hai? *no* likhein"
    )


def _reg_confirm(from_number: str, body: str) -> str:
    lower = body.strip().lower()
    if lower in ("yes", "y", "haan", "ha", "ok", "1", "sahi"):
        session       = sess.get_session(from_number)
        owner_name    = session.get("owner_name", "Shop Owner")
        business_name = session.get("business_name", "My Store")
        try:
            db.upsert_client(
                name=owner_name,
                whatsapp_number=from_number,
                business_name=business_name,
            )
            sess.clear_session(from_number)
            return (
                f"🎉 *Registration Ho Gayi!*\n"
                f"{DIV}\n"
                f"👋 Welcome, *{owner_name}*!\n"
                f"🏪 *{business_name}* ab Digital Khata par hai.\n\n"
                f"{MENU_TEXT}"
            )
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return "❌ Registration mein problem aayi.\nThodi der mein dobara try karein."
    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        return "↩️ Registration cancel kar di.\n\nDobara shuru karne ke liye kuch bhi likhein. 👋"
    return "Please *yes* ya *no* likhein:"


# ─────────────────────────────────────────────────────────
# IDLE MENU ROUTING
# ─────────────────────────────────────────────────────────

def _handle_idle_input(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("1", "add", "add entry", "entry", "naya entry"):
        sess.set_session(from_number, state="add_ask_name")
        return f"📝 *Naya Entry*\n{DIV}\n👤 *Customer ka naam* likhein:"

    if lower in ("2", "payment", "pay", "payment lena"):
        sess.set_session(from_number, state="pay_ask_name")
        return f"💰 *Payment Record Karna*\n{DIV}\n👤 *Customer ka naam* likhein:"

    if lower in ("3", "history", "hist", "history dekhna"):
        return f"📜 Format: `history <naam>`\n_Example: history Rahul_"

    if lower in ("4", "due", "check due", "baaki"):
        return f"💸 Format: `due <naam>`\n_Example: due Rahul_"

    if lower in ("5", "update", "entry update"):
        sess.set_session(from_number, state="upd_ask_name")
        return f"✏️ *Entry Update Karna*\n{DIV}\n👤 *Customer ka naam* likhein:"

    if lower in ("6", "delete", "undo", "delete / undo"):
        return (
            f"🗑️ *Delete / Undo*\n{DIV}\n"
            "↩️ Last entry undo: `undo Rahul`\n"
            "📅 Date se delete: `delete Rahul 2024-01-15`"
        )

    if lower in ("7", "team", "team manage"):
        return _cmd_list_members(client_id)

    return MENU_TEXT


# ─────────────────────────────────────────────────────────
# QUICK ENTRY  (only from IDLE)
# ─────────────────────────────────────────────────────────

def _looks_like_quick_entry(lower: str) -> bool:
    """
    Only trigger quick entry if:
    - At least 3 tokens
    - First token is pure name (no digits)
    - At least one later token has digits
    """
    tokens = lower.split()
    if len(tokens) < 3:
        return False
    if any(c.isdigit() for c in tokens[0]):
        return False
    return any(any(c.isdigit() for c in t) for t in tokens[1:])


def _handle_quick_entry(client_id: str, from_number: str, body: str) -> str:
    parsed = parse_voice_message(body)
    if not parsed["customer_name"] or not parsed["items"]:
        return MENU_TEXT

    prev_due    = db.get_previous_due(client_id, parsed["customer_name"])
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
        parsed["customer_name"], parsed["items"],
        parsed["current_total"], parsed["payment"],
        prev_due, updated_due,
    )


# ─────────────────────────────────────────────────────────
# ADD ENTRY FLOW
# ─────────────────────────────────────────────────────────

def _add_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    if not name:
        return "👤 Customer ka *sahi naam* likhein:"

    matches    = db.search_customers(client_id, name)
    suggestion = ""
    if matches and matches[0]["name"].lower() != name.lower():
        top = matches[0]
        suggestion = f"\n💡 _Similar: *{top['name']}* (baaki: ₹{top['total_due']:.0f})_"

    sess.set_session(from_number, state="add_ask_items", customer_name=name)
    return (
        f"👤 Customer: *{name}*{suggestion}\n\n"
        f"🛒 *Items aur amount* likhein:\n"
        f"{DIV_SM}\n"
        "_milk 20 bread 15_\n"
        "_rice 50 dal 30 oil 80_"
    )


def _add_ask_items(client_id: str, from_number: str, body: str) -> str:
    """
    FIX: Parse entire body as items — do NOT extract customer name here.
    Customer name was already captured in previous step.
    """
    items, total = parse_items_text(body)

    if not items:
        return (
            "❌ Items parse nahi hue. Please try karein.\n\n"
            "Format: `item amount item amount`\n"
            "Example: `milk 20 bread 15 rice 50`"
        )

    sess.set_session(from_number, state="add_ask_payment", items=items, current_total=total)
    items_display = "\n".join(f"  • {it['name']}: ₹{it['amount']:.0f}" for it in items)
    return (
        f"🛒 *Items record ho gaye:*\n{items_display}\n"
        f"{DIV}\n"
        f"💵 *Total: ₹{total:.0f}*\n\n"
        "💰 *Kitna payment liya?* (ya 0 likhen)"
    )


def _add_ask_payment(client_id: str, from_number: str, body: str) -> str:
    payment       = eval_amount(body)
    session       = sess.get_session(from_number)
    customer_name = session["customer_name"]
    items         = session["items"]
    current_total = session["current_total"]
    prev_due      = db.get_previous_due(client_id, customer_name)
    updated_due   = max(0.0, prev_due + current_total - payment)

    # Check if customer already has a phone
    from services.database import get_db as _gdb
    cust = _gdb().table("customers").select("phone") \
        .eq("client_id", client_id).ilike("name", customer_name.title()) \
        .limit(1).execute()
    has_phone = cust.data and cust.data[0].get("phone")

    if not has_phone:
        sess.set_session(from_number, state="add_ask_phone", payment=payment,
                         _prev_due=prev_due, _updated_due=updated_due)
        return (
            f"{_build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)}\n\n"
            "📱 *Customer ka phone number* likhein\n"
            "_(Bill WhatsApp pe bhejne ke liye)_\n"
            "Ya `skip` likhen:"
        )

    sess.set_session(from_number, state="add_confirm", payment=payment,
                     _prev_due=prev_due, _updated_due=updated_due)
    return _build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)


def _add_ask_phone(client_id: str, from_number: str, body: str) -> str:
    session       = sess.get_session(from_number)
    customer_name = session["customer_name"]
    items         = session["items"]
    current_total = session["current_total"]
    payment       = session["payment"]
    prev_due      = session.get("_prev_due", 0)
    updated_due   = session.get("_updated_due", 0)
    phone         = body.strip()

    if phone.lower() != "skip" and phone:
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")
        try:
            from services.database import get_db as _gdb
            db_c     = _gdb()
            existing = db_c.table("customers").select("id") \
                .eq("client_id", client_id).ilike("name", customer_name.title()) \
                .limit(1).execute()
            if existing.data:
                db_c.table("customers").update({"phone": phone}) \
                    .eq("id", existing.data[0]["id"]).execute()
            else:
                db_c.table("customers").insert({
                    "client_id": client_id, "name": customer_name.title(),
                    "phone": phone, "total_due": 0,
                }).execute()
        except Exception as e:
            logger.error(f"Could not save phone: {e}")

    sess.set_session(from_number, state="add_confirm",
                     _customer_phone=phone if phone.lower() != "skip" else "")
    return _build_confirm_text(customer_name, items, current_total, payment, prev_due, updated_due)


def _add_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ha", "ok", "save", "1", "sahi"):
        session = sess.get_session(from_number)
        record  = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=session["items"],
            current_total=session["current_total"],
            payment=session["payment"],
        )
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        _async_send_bill(client_id, record, session, from_number)
        return (
            f"✅ *Entry Save Ho Gayi!*\n"
            f"{DIV}\n"
            f"👤 {record['customer_name']}\n"
            f"📅 {record['date']}\n"
            f"🔴 Baaki: *₹{record['updated_due']:.0f}*\n"
            f"{DIV}\n"
            "🧾 Bill WhatsApp pe bheja ja raha hai...\n\n"
            "_Menu ke liye_ *menu* _likhein_"
        )

    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Entry cancel ho gayi.\n\n_Dobara ke liye_ *menu* _likhein_"

    return "⚠️ *yes* likhein — save  |  *no* likhein — cancel"


def _build_confirm_text(name, items, current_total, payment, prev_due, updated_due) -> str:
    lines = []
    for it in (items or []):
        qty  = it.get("qty", 1)
        rate = it.get("rate", it.get("amount", 0))
        amt  = float(it.get("amount", 0))
        nm   = it.get("name", "")
        if qty > 1 and rate != amt:
            lines.append(f"  • {nm}: {qty:.0f} × ₹{rate:.0f} = ₹{amt:.0f}")
        else:
            lines.append(f"  • {nm}: ₹{amt:.0f}")
    items_display = "\n".join(lines) if lines else "  _(Payment only)_"
    return (
        f"📋 *Entry Confirm Karein*\n"
        f"{DIV}\n"
        f"👤 Customer:      *{name}*\n"
        f"🛒 Items:\n{items_display}\n"
        f"{DIV}\n"
        f"💵 Bill Total:    ₹{current_total:.0f}\n"
        f"💰 Payment:       ₹{payment:.0f}\n"
        f"📊 Pehle ka Due:  ₹{prev_due:.0f}\n"
        f"🔴 Naya Baaki:    *₹{updated_due:.0f}*\n"
        f"{DIV}\n"
        "*yes* — Save  |  *no* — Cancel"
    )


def _async_send_bill(client_id: str, record: dict, session: dict, from_number: str):
    """Fire-and-forget bill sending in background thread."""
    def _send():
        try:
            from services.database import get_db as _gdb
            from utils.bill_generator import generate_and_send_bill
            db_c          = _gdb()
            client_rec    = db_c.table("clients").select("name, business_name") \
                .eq("id", client_id).limit(1).execute()
            business_name = "My Store"
            if client_rec.data:
                business_name = (client_rec.data[0].get("business_name")
                                 or client_rec.data[0].get("name", "My Store"))
            session_phone = session.get("_customer_phone", "")
            if session_phone:
                customer_phone = session_phone
            else:
                cust = db_c.table("customers").select("phone") \
                    .eq("client_id", client_id) \
                    .ilike("name", record["customer_name"]).limit(1).execute()
                customer_phone = cust.data[0].get("phone", "") if cust.data else ""
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
        except Exception as e:
            logger.error(f"Bill generation error: {e}")
    threading.Thread(target=_send, daemon=True).start()


# ─────────────────────────────────────────────────────────
# PAYMENT FLOW
# ─────────────────────────────────────────────────────────

def _pay_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    due  = db.get_latest_due(client_id, name)
    if due is None:
        return f"❌ *{name}* nahi mila.\nSahi naam likhein ya *menu* likhein:"
    sess.set_session(from_number, state="pay_ask_amount", customer_name=name)
    return (
        f"💰 *{name}*\n"
        f"{DIV_SM}\n"
        f"🔴 Current Baaki: *₹{due:.0f}*\n\n"
        "Kitna payment liya? *Amount* likhein:"
    )


def _pay_ask_amount(client_id: str, from_number: str, body: str) -> str:
    amount = eval_amount(body)
    if amount <= 0:
        return "❌ Valid amount likhein (sirf number):"
    session  = sess.get_session(from_number)
    name     = session["customer_name"]
    prev_due = db.get_previous_due(client_id, name)
    updated  = max(0.0, prev_due - amount)
    sess.set_session(from_number, state="pay_confirm", payment=amount)
    return (
        f"💰 *Payment Confirm Karein*\n"
        f"{DIV}\n"
        f"👤 Customer:    *{name}*\n"
        f"📊 Pehle Baaki: ₹{prev_due:.0f}\n"
        f"✅ Payment:     ₹{amount:.0f}\n"
        f"🟢 Naya Baaki:  *₹{updated:.0f}*\n"
        f"{DIV}\n"
        "*yes* — Confirm  |  *no* — Cancel"
    )


def _pay_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ok", "1", "sahi"):
        session = sess.get_session(from_number)
        record  = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=[], current_total=0.0,
            payment=session["payment"], notes="Payment entry",
        )
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return (
            f"✅ *Payment Record Ho Gayi!*\n"
            f"{DIV}\n"
            f"👤 {record['customer_name']}\n"
            f"💰 Liya: ₹{record['payment']:.0f}\n"
            f"🟢 Baaki: *₹{record['updated_due']:.0f}*\n\n"
            "_Menu ke liye_ *menu* _likhein_"
        )
    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Payment cancel.\n_Dobara ke liye_ *menu* _likhein_"
    return "*yes* ya *no* likhein:"


# ─────────────────────────────────────────────────────────
# HISTORY / DUE / UNDO / DELETE
# ─────────────────────────────────────────────────────────

def _cmd_history(client_id: str, customer_name: str) -> str:
    records = db.get_history(client_id, customer_name, limit=7)
    if not records:
        return f"📭 *{customer_name.title()}* ka koi record nahi mila."
    lines = [f"📜 *History: {customer_name.title()}*\n{DIV}"]
    for r in records:
        items_str = ", ".join(
            f"{it['name']} ₹{it['amount']:.0f}" for it in (r.get("items") or [])
        ) or "Payment only"
        lines.append(
            f"📅 *{r['date']}*\n"
            f"  🛒 {items_str}\n"
            f"  💰 Liya: ₹{r['payment']:.0f}  |  🔴 Baaki: ₹{r['updated_due']:.0f}"
        )
    lines.append(f"\n{DIV}\n_Menu ke liye_ *menu* _likhein_")
    return "\n\n".join(lines)


def _cmd_due(client_id: str, customer_name: str) -> str:
    due = db.get_latest_due(client_id, customer_name)
    if due is None:
        return f"❌ *{customer_name.title()}* nahi mila."
    status = "🟢 *Saaf!*" if due == 0 else f"🔴 *₹{due:.0f}* baaki"
    return (
        f"💸 *Baaki — {customer_name.title()}*\n"
        f"{DIV_SM}\n"
        f"{status}"
    )


def _cmd_undo(client_id: str, customer_name: str) -> str:
    ok = db.delete_last_record(client_id, customer_name)
    if ok:
        due = db.get_latest_due(client_id, customer_name) or 0
        return (
            f"↩️ *{customer_name.title()}* ki last entry undo ho gayi.\n"
            f"Updated Baaki: ₹{due:.0f}"
        )
    return f"❌ *{customer_name.title()}* ka koi record nahi mila."


def _cmd_delete_by_date(client_id: str, customer_name: str, date_str: str) -> str:
    from services.database import get_db as _gdb
    try:
        res = _gdb().table("records").select("id") \
            .eq("client_id", client_id) \
            .ilike("customer_name", customer_name.title()) \
            .eq("date", date_str).limit(1).execute()
        if not res.data:
            return f"❌ *{customer_name}* ka {date_str} wala record nahi mila."
        db.delete_record(res.data[0]["id"], client_id)
        return f"✅ *{customer_name.title()}* ka {date_str} wala record delete ho gaya."
    except Exception as e:
        logger.error(f"Delete by date error: {e}")
        return "❌ Error. Date format check karein: YYYY-MM-DD"


# ─────────────────────────────────────────────────────────
# UPDATE FLOW
# ─────────────────────────────────────────────────────────

def _upd_ask_name(client_id: str, from_number: str, body: str) -> str:
    name    = body.strip().title()
    records = db.get_history(client_id, name, limit=5)
    if not records:
        return f"❌ *{name}* ka koi record nahi.\nDusra naam likhein ya *menu*:"
    lines = [f"✏️ *{name}* — Recent Entries\n{DIV}"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. 📅 {r['date']} — ₹{r['current_total']:.0f} (paid ₹{r['payment']:.0f})")
    sess.set_session(from_number, state="upd_show_records",
                     customer_name=name, _records=[r["id"] for r in records])
    lines.append(f"\n{DIV}\nKaun sa update karein? *Number* (1-5) likhein:")
    return "\n".join(lines)


def _upd_show_records(client_id: str, from_number: str, body: str) -> str:
    try:
        idx = int(body.strip()) - 1
    except ValueError:
        return "Number likhein (1-5):"
    session    = sess.get_session(from_number)
    record_ids = session.get("_records", [])
    if idx < 0 or idx >= len(record_ids):
        return "❌ Invalid. Upar dikhaya hua number likhein:"
    sess.set_session(from_number, state="upd_ask_field", record_id=record_ids[idx])
    return (
        f"✏️ *Kya update karein?*\n{DIV}\n"
        "1️⃣  Items / Total\n"
        "2️⃣  Payment\n\n"
        "1 ya 2 likhein:"
    )


def _upd_ask_field(client_id: str, from_number: str, body: str) -> str:
    lower = body.strip().lower()
    if lower in ("1", "items", "total"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="items")
        return "🛒 Naye items likhein:\n_Example: milk 20 bread 15_"
    if lower in ("2", "payment", "pay"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="payment")
        return "💰 Naya payment amount likhein:"
    return "1 ya 2 likhein:"


def _upd_ask_value(client_id: str, from_number: str, body: str) -> str:
    session   = sess.get_session(from_number)
    record_id = session.get("record_id")
    field     = session.get("_upd_field")
    try:
        if field == "items":
            items, total = parse_items_text(body)
            db.update_record(record_id, client_id, items=items, current_total=total)
        elif field == "payment":
            amount = eval_amount(body)
            db.update_record(record_id, client_id, payment=amount)
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "✅ *Entry update ho gayi!*\nDues recalculate ho gaye.\n\n_Menu ke liye_ *menu* _likhein_"
    except Exception as e:
        logger.error(f"Update error: {e}")
        return "❌ Update nahi hua. Dobara try karein ya *menu* likhein."


# ─────────────────────────────────────────────────────────
# TEAM / MULTI-MEMBER ACCESS
# ─────────────────────────────────────────────────────────

def _cmd_add_member(client_id: str, member_number: str) -> str:
    if not member_number.isdigit() or len(member_number) < 10:
        return (
            "❌ Sahi number nahi hai.\n"
            "Format: `add member 919XXXXXXXXX`\n"
            "_Country code ke saath (India: 91)_"
        )
    try:
        if db.is_member_exists(client_id, member_number):
            return f"⚠️ *+{member_number}* already aapki team mein hai!"
        db.add_member(client_id, member_number)
        return (
            f"✅ *Member Add Ho Gaya!*\n"
            f"{DIV}\n"
            f"📱 +{member_number}\n\n"
            "Ab yeh number aapki dukaan ka data access kar sakta hai.\n"
            "_Unhe batayein ki is number pe_ *hi* _likhein_ 👋"
        )
    except Exception as e:
        logger.error(f"Add member error: {e}")
        return "❌ Member add nahi hua. Dobara try karein."


def _cmd_remove_member(client_id: str, member_number: str) -> str:
    try:
        db.remove_member(client_id, member_number)
        return f"✅ *+{member_number}* ka access remove ho gaya."
    except Exception as e:
        logger.error(f"Remove member error: {e}")
        return "❌ Remove nahi hua. Dobara try karein."


def _cmd_list_members(client_id: str) -> str:
    try:
        members = db.get_members(client_id)
        if not members:
            return (
                f"👥 *Team Members*\n{DIV}\n"
                "Abhi koi member nahi hai.\n\n"
                "Add karne ke liye:\n"
                "`add member 919XXXXXXXXX`"
            )
        lines = [f"👥 *Aapki Team*\n{DIV}"]
        for m in members:
            icon = "👑" if m["role"] == "owner" else "👤"
            lines.append(f"{icon} +{m['whatsapp_number']}  _{m['role']}_")
        lines.append(
            f"\n{DIV}\n"
            "➕ Add: `add member 919XXXXXXXXX`\n"
            "➖ Remove: `remove member 919XXXXXXXXX`"
        )
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"List members error: {e}")
        return "❌ Team list nahi mili. Dobara try karein."


# ─────────────────────────────────────────────────────────
# BACKWARD COMPATIBILITY — old public functions kept
# ─────────────────────────────────────────────────────────

def handle_unregistered(from_number: str) -> str:
    """Legacy alias — kept for backward compatibility."""
    return _handle_unregistered(from_number, "")


def reg_ask_name(from_number: str, body: str) -> str:
    return _reg_ask_name(from_number, body)


def reg_ask_business(from_number: str, body: str) -> str:
    return _reg_ask_business(from_number, body)


def reg_confirm(from_number: str, body: str) -> str:
    return _reg_confirm(from_number, body)
