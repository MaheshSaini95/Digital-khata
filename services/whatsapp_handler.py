"""
services/whatsapp_handler.py — Digital Khata v2
New features:
  - Self-service registration flow with OTP
  - Multi-number access (add/remove secondary numbers)
  - Backdated entry support (enter old date data)
  - Professional automation commands
  - Settings menu
"""
from __future__ import annotations
import re
import logging
from datetime import date, datetime
from typing import Optional

from services import database as db
from services import session as sess
from utils.calculator import (parse_voice_message, parse_items_text,
                               eval_amount, format_items_list)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# MENU TEXTS
# ─────────────────────────────────────────────────────────────

MENU_TEXT = """📒 *Digital Khata* — Main Menu

Reply with a number:

1️⃣  Add Entry
2️⃣  Record Payment
3️⃣  View History
4️⃣  Check Due
5️⃣  Update Entry
6️⃣  Delete / Undo
7️⃣  Old Date Entry
8️⃣  Settings

📌 *Quick commands:*
• `history Ram`  • `due Ram`
• `undo Ram`     • `due all`"""

SETTINGS_MENU = """⚙️ *Settings*

1️⃣  Add secondary number
2️⃣  View my numbers
3️⃣  Remove a number
4️⃣  Update shop name
5️⃣  Monthly report
6️⃣  Send reminders

Reply *menu* to go back."""

HELP_TEXT = """ℹ️ *Digital Khata Help*

*Add entry:*
_Ram loki 10*10 chuchu 20*50 payment 100_

*Old date entry:*
Type: *7* then enter date as DD-MM-YYYY

*Check due:*  _due Ram_
*All dues:*   _due all_
*History:*    _history Ram_
*Undo:*       _undo Ram_

*Add number:*  _addnumber 91XXXXXXXXXX_

Reply *menu* anytime."""

REGISTRATION_WELCOME = """👋 *Welcome to Digital Khata!*

India's smartest WhatsApp ledger system.
Manage your khata, send bills, track dues — all on WhatsApp!

Would you like to *register* your shop?

Reply *YES* to start registration
Reply *NO* to cancel"""


# ─────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────

def handle_message(from_number: str, body: str,
                   media_url: Optional[str] = None) -> str:
    """Route incoming message to correct handler."""

    # Resolve client
    client = db.get_client_by_number(from_number)

    # ── Unregistered user → registration flow ─────────────────
    if not client:
        return _handle_registration(from_number, body)

    client_id = client["id"]
    sess.set_session(from_number, client_id=client_id)
    session = sess.get_session(from_number)

    # ── Voice processing ──────────────────────────────────────
    if media_url and not body:
        try:
            from services.voice import transcribe_from_url
            body = transcribe_from_url(media_url)
            logger.info(f"Voice→text: {body!r}")
        except Exception as e:
            logger.error(f"Voice failed: {e}")
            return "❌ Could not process voice. Please type your entry."

    body  = (body or "").strip()
    lower = body.lower()

    # ── Global shortcuts ──────────────────────────────────────
    if lower in ("hi", "hello", "menu", "start", "0", "main menu"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return MENU_TEXT

    if lower in ("help", "?"):
        return HELP_TEXT

    if lower in ("settings", "setting", "8"):
        sess.set_session(from_number, state="settings")
        return SETTINGS_MENU

    # ── One-shot commands ─────────────────────────────────────
    if lower.startswith(("history ", "hist ")):
        name = body.split(" ", 1)[1].strip()
        return _cmd_history(client_id, name)

    if lower == "due all":
        return _cmd_due_all(client_id)

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

    if lower.startswith("addnumber "):
        new_num = body.split(" ", 1)[1].strip()
        return _cmd_add_number(client_id, from_number, new_num)

    if lower.startswith("removenumber "):
        num = body.split(" ", 1)[1].strip()
        return _cmd_remove_number(client_id, from_number, num)

    # ── FSM routing ───────────────────────────────────────────
    state = session.get("state", "idle")

    if state == "idle":
        if _looks_like_quick_entry(lower):
            return _handle_quick_entry(client_id, from_number, body)
        return _handle_idle_input(client_id, from_number, lower)

    # Add entry flow
    if state == "add_ask_name":    return _add_ask_name(client_id, from_number, body)
    if state == "add_ask_items":   return _add_ask_items(client_id, from_number, body)
    if state == "add_ask_payment": return _add_ask_payment(client_id, from_number, body)
    if state == "add_ask_phone":   return _add_ask_phone(client_id, from_number, body)
    if state == "add_confirm":     return _add_confirm(client_id, from_number, lower)

    # Payment flow
    if state == "pay_ask_name":   return _pay_ask_name(client_id, from_number, body)
    if state == "pay_ask_amount": return _pay_ask_amount(client_id, from_number, body)
    if state == "pay_confirm":    return _pay_confirm(client_id, from_number, lower)

    # Old date entry flow
    if state == "old_ask_date":    return _old_ask_date(client_id, from_number, body)
    if state == "old_ask_name":    return _old_ask_name(client_id, from_number, body)
    if state == "old_ask_items":   return _old_ask_items(client_id, from_number, body)
    if state == "old_ask_payment": return _old_ask_payment(client_id, from_number, body)
    if state == "old_confirm":     return _old_confirm(client_id, from_number, lower)

    # Update flow
    if state == "upd_ask_name":     return _upd_ask_name(client_id, from_number, body)
    if state == "upd_show_records": return _upd_show_records(client_id, from_number, body)
    if state == "upd_ask_field":    return _upd_ask_field(client_id, from_number, body)
    if state == "upd_ask_value":    return _upd_ask_value(client_id, from_number, body)

    # Settings flow
    if state == "settings":           return _settings_input(client_id, from_number, lower, body)
    if state == "settings_add_num":   return _settings_add_num(client_id, from_number, body)
    if state == "settings_shop_name": return _settings_shop_name(client_id, from_number, body)

    # Fallback
    sess.clear_session(from_number)
    sess.set_session(from_number, client_id=client_id)
    return MENU_TEXT


# ─────────────────────────────────────────────────────────────
# REGISTRATION FLOW
# ─────────────────────────────────────────────────────────────

def _handle_registration(from_number: str, body: str) -> str:
    """Multi-step registration for new users."""
    from services.registration import (generate_otp, save_otp,
                                        verify_otp, complete_registration)

    session  = sess.get_session(from_number)
    state    = session.get("state", "unreg_welcome")
    lower    = (body or "").strip().lower()

    # Step 1 — Welcome
    if state == "unreg_welcome":
        sess.set_session(from_number, state="unreg_confirm")
        return REGISTRATION_WELCOME

    # Step 2 — Confirm registration
    if state == "unreg_confirm":
        if lower in ("yes", "y", "haan", "ha", "register", "ok"):
            sess.set_session(from_number, state="unreg_ask_name")
            return (
                "👤 *Step 1 of 3 — Your Name*\n\n"
                "Enter your *full name*:\n"
                "_Example: Mahesh Saini_"
            )
        sess.clear_session(from_number)
        return "No problem! Send *hi* anytime to register."

    # Step 3 — Owner name
    if state == "unreg_ask_name":
        name = body.strip().title()
        if len(name) < 2:
            return "Please enter a valid name (at least 2 characters):"
        sess.set_session(from_number, state="unreg_ask_business",
                         reg_owner_name=name)
        return (
            f"✅ Name: *{name}*\n\n"
            f"🏪 *Step 2 of 3 — Shop Name*\n\n"
            f"Enter your *shop / business name*:\n"
            f"_Example: Shri Shyam Vegetable Company_"
        )

    # Step 4 — Business name
    if state == "unreg_ask_business":
        biz = body.strip()
        if len(biz) < 2:
            return "Please enter a valid shop name:"
        sess.set_session(from_number, state="unreg_ask_address",
                         reg_business_name=biz)
        return (
            f"✅ Shop: *{biz}*\n\n"
            f"📍 *Step 3 of 3 — Address*\n\n"
            f"Enter your *city or address*:\n"
            f"_Example: Muhana Mandi, Jaipur_"
        )

    # Step 5 — Address → Send OTP
    if state == "unreg_ask_address":
        address = body.strip()
        owner_name    = session.get("reg_owner_name", "")
        business_name = session.get("reg_business_name", "")

        otp = generate_otp()
        context = {
            "owner_name":    owner_name,
            "business_name": business_name,
            "address":       address,
        }
        save_otp(from_number, otp, context)
        sess.set_session(from_number, state="unreg_verify_otp")

        logger.info(f"OTP for {from_number}: {otp}")

        return (
            f"📋 *Registration Summary:*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 Name: *{owner_name}*\n"
            f"🏪 Shop: *{business_name}*\n"
            f"📍 Address: *{address}*\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"🔐 *Your OTP: {otp}*\n\n"
            f"Please enter the OTP above to complete registration.\n"
            f"_(OTP valid for 10 minutes)_"
        )

    # Step 6 — Verify OTP
    if state == "unreg_verify_otp":
        context = verify_otp(from_number, body.strip())
        if context is None:
            return (
                "❌ *Invalid or expired OTP.*\n\n"
                "Please enter the correct OTP, or send *hi* to restart registration."
            )

        client = complete_registration(from_number, context)
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client["id"])

        return (
            f"🎉 *Welcome to Digital Khata!*\n\n"
            f"✅ Registration successful!\n\n"
            f"🏪 *{client['business_name']}*\n"
            f"👤 {client['owner_name']}\n\n"
            f"Your account is ready. Here's what you can do:\n\n"
            + MENU_TEXT
        )

    # Default — show welcome again
    sess.set_session(from_number, state="unreg_welcome")
    return REGISTRATION_WELCOME


# ─────────────────────────────────────────────────────────────
# IDLE → MENU
# ─────────────────────────────────────────────────────────────

def _handle_idle_input(client_id: str, from_number: str, lower: str) -> str:
    mapping = {
        ("1", "add", "add entry", "entry"): ("add_ask_name",
            "📝 *Add Entry*\n\nEnter *customer name*:"),
        ("2", "payment", "pay"): ("pay_ask_name",
            "💰 *Record Payment*\n\nEnter *customer name*:"),
        ("5", "update"): ("upd_ask_name",
            "✏️ *Update Entry*\n\nEnter *customer name*:"),
        ("7", "old", "old entry", "old date", "backdate"): ("old_ask_date",
            "📅 *Old Date Entry*\n\nEnter the *date* (DD-MM-YYYY):\n_Example: 15-03-2025_"),
    }
    for keys, (state, prompt) in mapping.items():
        if lower in keys:
            sess.set_session(from_number, state=state)
            return prompt

    if lower in ("3", "history", "hist"):
        return "📜 Type: *history <name>*\nExample: _history Ram_"
    if lower in ("4", "due", "check due"):
        return "💸 Type: *due <name>*  or  *due all*"
    if lower in ("6", "delete", "undo"):
        return "🗑️ *Delete / Undo*\n\n• Last entry: _undo Ram_\n• By date: _delete Ram 2025-03-15_"
    if lower in ("8", "settings", "setting"):
        sess.set_session(from_number, state="settings")
        return SETTINGS_MENU

    return MENU_TEXT


# ─────────────────────────────────────────────────────────────
# QUICK ENTRY
# ─────────────────────────────────────────────────────────────

def _looks_like_quick_entry(lower: str) -> bool:
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

    sess.set_session(from_number, state="add_confirm",
                     customer_name=parsed["customer_name"],
                     items=parsed["items"],
                     current_total=parsed["current_total"],
                     payment=parsed["payment"])

    return _build_confirm_text(parsed["customer_name"], parsed["items"],
                                parsed["current_total"], parsed["payment"],
                                prev_due, updated_due)


# ─────────────────────────────────────────────────────────────
# ADD ENTRY FLOW
# ─────────────────────────────────────────────────────────────

def _add_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    if not name:
        return "Please enter a valid customer name:"
    matches = db.search_customers(client_id, name)
    sess.set_session(from_number, state="add_ask_items", customer_name=name)
    suggestion = ""
    if matches and matches[0]["name"].lower() != name.lower():
        t = matches[0]
        suggestion = f"\n💡 Similar: *{t['name']}* (due: ₹{t['total_due']:.0f})\n"
    return (f"👤 Customer: *{name}*{suggestion}\n\nEnter *items and amounts*:\n"
            f"• _loki 10*10 chuchu 20*50_\n• _milk 20 bread 15_")


def _add_ask_items(client_id: str, from_number: str, body: str) -> str:
    items, total = parse_items_text(body)
    if not items:
        return "❌ Could not parse items.\nFormat: _item qty*rate_ or _item amount_\nExample: _loki 10*10 milk 20_"
    sess.set_session(from_number, state="add_ask_payment",
                     items=items, current_total=total)
    return (f"🛒 *Items:*\n{format_items_list(items)}\n"
            f"━━━━━━━━━━━━━━━\n*Total: ₹{total:.0f}*\n\n"
            f"Enter *payment received* (or 0):")


def _add_ask_payment(client_id: str, from_number: str, body: str) -> str:
    payment  = eval_amount(body)
    session  = sess.get_session(from_number)
    name     = session["customer_name"]
    items    = session["items"]
    total    = session["current_total"]
    prev_due = db.get_previous_due(client_id, name)
    updated  = max(0.0, prev_due + total - payment)

    # Check DB for existing phone number
    db_client = db.get_db()
    cust = db_client.table("customers").select("phone") \
        .eq("client_id", client_id).ilike("name", name) \
        .limit(1).execute()
    existing_phone = cust.data[0].get("phone", "").strip() if cust.data else ""

    if existing_phone:
        # Phone already saved — skip asking, go straight to confirm
        # Store phone in session for bill sending
        sess.set_session(from_number, state="add_confirm",
                         payment=payment, _prev_due=prev_due,
                         _updated_due=updated,
                         _customer_phone=existing_phone)
        logger.info(f"Phone already saved for {name}: {existing_phone} — skipping phone ask")
        return _build_confirm_text(name, items, total, payment, prev_due, updated)
    else:
        # No phone yet — ask once
        sess.set_session(from_number, state="add_ask_phone",
                         payment=payment, _prev_due=prev_due, _updated_due=updated)
        return (
            f"{_build_confirm_text(name, items, total, payment, prev_due, updated)}\n\n"
            f"📱 Enter customer *phone number* to send bill\n_(or type *skip*)_"
        )


def _add_ask_phone(client_id: str, from_number: str, body: str) -> str:
    session  = sess.get_session(from_number)
    raw      = body.strip()
    phone    = ""

    if raw.lower() != "skip" and raw:
        phone = raw.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")
        try:
            db_client = db.get_db()
            existing = db_client.table("customers").select("id") \
                .eq("client_id", client_id) \
                .ilike("name", session["customer_name"].title()) \
                .limit(1).execute()
            if existing.data:
                db_client.table("customers").update({"phone": phone}) \
                    .eq("id", existing.data[0]["id"]).execute()
        except Exception as e:
            logger.error(f"Phone save error: {e}")

    sess.set_session(from_number, state="add_confirm",
                     _customer_phone=phone if raw.lower() != "skip" else "")
    return _build_confirm_text(
        session["customer_name"], session["items"],
        session["current_total"], session["payment"],
        session.get("_prev_due", 0), session.get("_updated_due", 0)
    )


def _add_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ha", "ok", "save", "1"):
        session = sess.get_session(from_number)
        record  = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=session["items"],
            current_total=session["current_total"],
            payment=session["payment"],
        )
        _send_bill_async(client_id, from_number, record, session)
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return (f"✅ *Entry saved!*\n\n👤 {record['customer_name']}\n"
                f"📅 {record['date']}\n💰 Due: ₹{record['updated_due']:.0f}\n\n"
                f"🧾 Bill is being sent...\nReply *menu* for more options.")

    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Entry cancelled.\n\nReply *menu* to start over."

    return "Reply *yes* to save or *no* to cancel."


# ─────────────────────────────────────────────────────────────
# OLD DATE ENTRY FLOW (Backdating)
# ─────────────────────────────────────────────────────────────

def _parse_date_input(text: str) -> Optional[date]:
    """Parse user date input in multiple formats."""
    text = text.strip()
    formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y",
        "%Y-%m-%d", "%d %m %Y", "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _old_ask_date(client_id: str, from_number: str, body: str) -> str:
    parsed_date = _parse_date_input(body)
    if not parsed_date:
        return (
            "❌ Could not read the date.\n\n"
            "Please enter in format *DD-MM-YYYY*\n"
            "Example: _15-03-2025_"
        )
    if parsed_date > date.today():
        return "❌ Date cannot be in the future. Please enter a past date:"

    sess.set_session(from_number, state="old_ask_name",
                     entry_date=str(parsed_date))
    return (
        f"📅 Date: *{parsed_date.strftime('%d %B %Y')}*\n\n"
        f"👤 Enter *customer name*:"
    )


def _old_ask_name(client_id: str, from_number: str, body: str) -> str:
    name    = body.strip().title()
    session = sess.get_session(from_number)
    entry_date = date.fromisoformat(session["entry_date"])

    # Get due AS OF that date
    prev_due = db.get_previous_due(client_id, name, before_date=entry_date)

    sess.set_session(from_number, state="old_ask_items", customer_name=name)
    return (
        f"👤 *{name}* on {entry_date.strftime('%d %b %Y')}\n"
        f"📊 Due as of that date: ₹{prev_due:.0f}\n\n"
        f"Enter *items and amounts*:"
    )


def _old_ask_items(client_id: str, from_number: str, body: str) -> str:
    items, total = parse_items_text(body)
    if not items:
        return "❌ Could not parse items. Try: _loki 10*10 milk 20_"
    sess.set_session(from_number, state="old_ask_payment",
                     items=items, current_total=total)
    return (f"🛒 *Items:*\n{format_items_list(items)}\n"
            f"━━━━━━━━━━━━━━━\n*Total: ₹{total:.0f}*\n\nEnter *payment* (or 0):")


def _old_ask_payment(client_id: str, from_number: str, body: str) -> str:
    payment  = eval_amount(body)
    session  = sess.get_session(from_number)
    name     = session["customer_name"]
    items    = session["items"]
    total    = session["current_total"]
    entry_date = date.fromisoformat(session["entry_date"])
    prev_due = db.get_previous_due(client_id, name, before_date=entry_date)
    updated  = max(0.0, prev_due + total - payment)

    sess.set_session(from_number, state="old_confirm",
                     payment=payment, _prev_due=prev_due, _updated_due=updated)

    return (
        f"📋 *Confirm Old Entry*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 Date: *{entry_date.strftime('%d %B %Y')}*\n"
        f"👤 Customer: *{name}*\n"
        f"🛒 Items:\n{format_items_list(items)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 Total: ₹{total:.0f}\n"
        f"💰 Payment: ₹{payment:.0f}\n"
        f"📊 Prev Due: ₹{prev_due:.0f}\n"
        f"🔴 New Due: *₹{updated:.0f}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚠️ _This will recalculate all future entries for this customer._\n\n"
        f"Reply *yes* to save or *no* to cancel."
    )


def _old_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ok", "1"):
        session    = sess.get_session(from_number)
        entry_date = date.fromisoformat(session["entry_date"])
        record     = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=session["items"],
            current_total=session["current_total"],
            payment=session["payment"],
            record_date=entry_date,
        )
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return (
            f"✅ *Old entry saved & dues recalculated!*\n\n"
            f"👤 {record['customer_name']}\n"
            f"📅 {record['date']}\n"
            f"💰 Updated Due: ₹{record['updated_due']:.0f}\n\n"
            f"All future entries have been recalculated.\n"
            f"Reply *menu* for more options."
        )
    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Cancelled.\n\nReply *menu* to start over."
    return "Reply *yes* to save or *no* to cancel."


# ─────────────────────────────────────────────────────────────
# PAYMENT FLOW
# ─────────────────────────────────────────────────────────────

def _pay_ask_name(client_id: str, from_number: str, body: str) -> str:
    name = body.strip().title()
    due  = db.get_latest_due(client_id, name)
    if due is None:
        return f"❌ Customer *{name}* not found.\nEnter correct name or *menu*:"
    sess.set_session(from_number, state="pay_ask_amount", customer_name=name)
    return f"💰 *{name}* — Current due: *₹{due:.0f}*\n\nEnter *payment amount*:"


def _pay_ask_amount(client_id: str, from_number: str, body: str) -> str:
    amount = eval_amount(body)
    if amount <= 0:
        return "Please enter a valid payment amount:"
    session  = sess.get_session(from_number)
    name     = session["customer_name"]
    prev_due = db.get_previous_due(client_id, name)
    updated  = max(0.0, prev_due - amount)
    sess.set_session(from_number, state="pay_confirm", payment=amount)
    return (
        f"💰 *Payment Confirmation*\n━━━━━━━━━━━━━━━\n"
        f"👤 *{name}*\n"
        f"📊 Current Due: ₹{prev_due:.0f}\n"
        f"✅ Payment: ₹{amount:.0f}\n"
        f"🟢 New Due: *₹{updated:.0f}*\n━━━━━━━━━━━━━━━\n"
        f"Reply *yes* to confirm or *no* to cancel."
    )


def _pay_confirm(client_id: str, from_number: str, lower: str) -> str:
    if lower in ("yes", "y", "haan", "ok", "1"):
        session = sess.get_session(from_number)
        record  = db.add_record(
            client_id=client_id,
            customer_name=session["customer_name"],
            items=[], current_total=0.0,
            payment=session["payment"], notes="Payment entry",
        )
        _send_bill_async(client_id, from_number, record, session)
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return (f"✅ *Payment recorded!*\n\n👤 {record['customer_name']}\n"
                f"💰 Paid: ₹{record['payment']:.0f}\n"
                f"🟢 Remaining: ₹{record['updated_due']:.0f}\n\n"
                f"Reply *menu* for more options.")
    if lower in ("no", "n", "nahi", "cancel", "2"):
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "❌ Payment cancelled."
    return "Reply *yes* to confirm or *no* to cancel."


# ─────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────

def _cmd_history(client_id: str, customer_name: str) -> str:
    records = db.get_history(client_id, customer_name, limit=7)
    if not records:
        return f"📭 No records found for *{customer_name.title()}*."
    lines = [f"📜 *History: {customer_name.title()}*\n"]
    for r in records:
        items_str = ", ".join(
            f"{it['name']} ₹{it['amount']:.0f}" for it in (r.get("items") or [])
        ) or "Payment only"
        lines.append(f"📅 *{r['date']}*\n  {items_str}\n  Paid: ₹{r['payment']:.0f} | Due: ₹{r['updated_due']:.0f}\n")
    lines.append("Reply *menu* for more options.")
    return "\n".join(lines)


def _cmd_due(client_id: str, customer_name: str) -> str:
    due = db.get_latest_due(client_id, customer_name)
    if due is None:
        return f"❌ Customer *{customer_name.title()}* not found."
    emoji = "🟢" if due == 0 else "🔴"
    return f"{emoji} *{customer_name.title()}*\nOutstanding due: *₹{due:.0f}*"


def _cmd_due_all(client_id: str) -> str:
    """Show all customers with pending dues."""
    overdue = db.get_overdue_customers(client_id, min_due=1)
    if not overdue:
        return "🟢 *No pending dues!*\n\nAll customers are cleared."
    total = sum(float(c["total_due"]) for c in overdue)
    lines = [f"📊 *All Pending Dues*\n*Total: ₹{total:.0f}*\n"]
    for c in overdue[:15]:
        lines.append(f"🔴 *{c['name']}*: ₹{c['total_due']:.0f}")
    if len(overdue) > 15:
        lines.append(f"\n_...and {len(overdue)-15} more_")
    return "\n".join(lines)


def _cmd_undo(client_id: str, customer_name: str) -> str:
    ok  = db.delete_last_record(client_id, customer_name)
    due = db.get_latest_due(client_id, customer_name) or 0
    if ok:
        return f"✅ Last entry for *{customer_name.title()}* undone.\nUpdated due: ₹{due:.0f}"
    return f"❌ No records found for *{customer_name.title()}*."


def _cmd_delete_by_date(client_id: str, customer_name: str,
                         date_str: str) -> str:
    db_client = db.get_db()
    try:
        res = db_client.table("records").select("id") \
            .eq("client_id", client_id) \
            .ilike("customer_name", customer_name.title()) \
            .eq("date", date_str).limit(1).execute()
        if not res.data:
            return f"❌ No record found for *{customer_name}* on *{date_str}*."
        db.delete_record(res.data[0]["id"], client_id)
        return f"✅ Record deleted for *{customer_name.title()}* on *{date_str}*."
    except Exception as e:
        logger.error(f"Delete by date error: {e}")
        return "❌ Error. Check date format: YYYY-MM-DD"


def _cmd_add_number(client_id: str, from_number: str, new_num: str) -> str:
    """Add a secondary number to this account via command."""
    from services.registration import add_secondary_number
    num = new_num.replace(" ", "").replace("-", "")
    if not num.startswith("+"):
        num = "+" + num
    if db.is_number_registered(num):
        return f"❌ Number *{num}* is already registered."
    ok = add_secondary_number(client_id, num)
    if ok:
        return (f"✅ *{num}* added as a secondary number.\n\n"
                f"Now both numbers can access this account.")
    return "❌ Failed to add number. Please try again."


def _cmd_remove_number(client_id: str, from_number: str, num: str) -> str:
    from services.registration import remove_secondary_number
    if not num.startswith("+"):
        num = "+" + num
    ok = remove_secondary_number(client_id, num)
    if ok:
        return f"✅ Number *{num}* removed from your account."
    return "❌ Cannot remove primary number or number not found."


# ─────────────────────────────────────────────────────────────
# SETTINGS FLOW
# ─────────────────────────────────────────────────────────────

def _settings_input(client_id: str, from_number: str,
                     lower: str, body: str) -> str:
    if lower in ("1", "add number", "add secondary"):
        sess.set_session(from_number, state="settings_add_num")
        return "📱 Enter the *new WhatsApp number* to add:\nExample: _+919876543210_"

    if lower in ("2", "view numbers", "my numbers"):
        from services.registration import get_client_numbers
        numbers = get_client_numbers(client_id)
        lines = ["📱 *Your Registered Numbers:*\n"]
        for n in numbers:
            icon = "🟢" if n["label"] == "primary" else "🔵"
            lines.append(f"{icon} {n['number']} _{n['label']}_")
        lines.append("\nReply *menu* to go back.")
        sess.set_session(from_number, state="idle")
        return "\n".join(lines)

    if lower in ("3", "remove number"):
        sess.set_session(from_number, state="idle")
        return "To remove a number, type:\n_removenumber +91XXXXXXXXXX_"

    if lower in ("4", "update shop", "shop name"):
        sess.set_session(from_number, state="settings_shop_name")
        return "🏪 Enter new *shop / business name*:"

    if lower in ("5", "monthly report", "report"):
        now = datetime.now()
        summary = db.get_monthly_summary(client_id, now.year, now.month)
        if not summary:
            sess.set_session(from_number, state="idle")
            return f"📊 No records for {now.strftime('%B %Y')}."
        total_sales = sum(r["total_sales"] for r in summary)
        total_paid  = sum(r["total_payments"] for r in summary)
        lines = [f"📊 *{now.strftime('%B %Y')} Report*\n"
                 f"💵 Sales: ₹{total_sales:.0f}\n"
                 f"✅ Collected: ₹{total_paid:.0f}\n"
                 f"🔴 Pending: ₹{total_sales-total_paid:.0f}\n\n"
                 f"*Top customers:*"]
        for r in sorted(summary, key=lambda x: -x["total_sales"])[:5]:
            pending = r["total_sales"] - r["total_payments"]
            lines.append(f"• {r['customer_name']}: ₹{pending:.0f}")
        sess.set_session(from_number, state="idle")
        return "\n".join(lines)

    if lower in ("6", "reminders", "send reminders"):
        _send_reminders_async(client_id)
        sess.set_session(from_number, state="idle")
        return "📱 Sending reminders to all customers with pending dues..."

    sess.set_session(from_number, state="idle")
    return MENU_TEXT


def _settings_add_num(client_id: str, from_number: str, body: str) -> str:
    from services.registration import add_secondary_number
    num = body.strip().replace(" ", "").replace("-", "")
    if not num.startswith("+"):
        num = "+" + num
    if db.is_number_registered(num):
        sess.set_session(from_number, state="idle")
        return f"❌ *{num}* is already registered to an account."
    ok = add_secondary_number(client_id, num)
    sess.set_session(from_number, state="idle")
    if ok:
        return (f"✅ *{num}* added successfully!\n\n"
                f"This number can now access your khata.\nReply *menu* to continue.")
    return "❌ Failed to add number. Please try again."


def _settings_shop_name(client_id: str, from_number: str, body: str) -> str:
    new_name = body.strip()
    if len(new_name) < 2:
        return "Please enter a valid shop name:"
    db_client = db.get_db()
    db_client.table("clients").update({"business_name": new_name}) \
        .eq("id", client_id).execute()
    sess.set_session(from_number, state="idle")
    return f"✅ Shop name updated to *{new_name}*\n\nReply *menu* to continue."


# ─────────────────────────────────────────────────────────────
# UPDATE FLOW
# ─────────────────────────────────────────────────────────────

def _upd_ask_name(client_id: str, from_number: str, body: str) -> str:
    name    = body.strip().title()
    records = db.get_history(client_id, name, limit=5)
    if not records:
        return f"❌ No records for *{name}*.\nEnter another name or *menu*:"
    lines = [f"✏️ *Recent entries for {name}:*\n"]
    for i, r in enumerate(records, 1):
        items_str = ", ".join(it["name"] for it in (r.get("items") or [])) or "Payment"
        lines.append(f"{i}. 📅 {r['date']} — {items_str} — ₹{r['current_total']:.0f}")
    sess.set_session(from_number, state="upd_show_records",
                     customer_name=name,
                     _records=[r["id"] for r in records])
    lines.append("\nReply with *number* (1-5) to select:")
    return "\n".join(lines)


def _upd_show_records(client_id: str, from_number: str, body: str) -> str:
    try:
        idx = int(body.strip()) - 1
    except ValueError:
        return "Please reply with a number (1-5):"
    session    = sess.get_session(from_number)
    record_ids = session.get("_records", [])
    if idx < 0 or idx >= len(record_ids):
        return "Invalid. Reply with number shown above:"
    sess.set_session(from_number, state="upd_ask_field",
                     record_id=record_ids[idx])
    return "What to update?\n\n1. *Items / Total*\n2. *Payment*\n\nReply 1 or 2:"


def _upd_ask_field(client_id: str, from_number: str, body: str) -> str:
    lower = body.strip().lower()
    if lower in ("1", "items", "total"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="items")
        return "Enter new items:\n_loki 10*10 milk 20_"
    if lower in ("2", "payment", "pay"):
        sess.set_session(from_number, state="upd_ask_value", _upd_field="payment")
        return "Enter new payment amount:"
    return "Reply *1* for items or *2* for payment:"


def _upd_ask_value(client_id: str, from_number: str, body: str) -> str:
    session   = sess.get_session(from_number)
    record_id = session.get("record_id")
    field     = session.get("_upd_field")
    try:
        if field == "items":
            items, total = parse_items_text(body)
            db.update_record(record_id, client_id, items=items, current_total=total)
        elif field == "payment":
            db.update_record(record_id, client_id, payment=eval_amount(body))
        sess.clear_session(from_number)
        sess.set_session(from_number, client_id=client_id)
        return "✅ Entry updated & dues recalculated!\n\nReply *menu* for more options."
    except Exception as e:
        logger.error(f"Update error: {e}")
        return "❌ Update failed. Reply *menu* to try again."


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _build_confirm_text(name, items, current_total,
                         payment, prev_due, updated_due) -> str:
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
        f"📋 *Confirm Entry*\n━━━━━━━━━━━━━━━\n"
        f"👤 Customer: *{name}*\n🛒 Items:\n{items_display}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 Total: ₹{current_total:.0f}\n"
        f"💰 Payment: ₹{payment:.0f}\n"
        f"📊 Prev Due: ₹{prev_due:.0f}\n"
        f"🔴 New Due: *₹{updated_due:.0f}*\n━━━━━━━━━━━━━━━\n"
        f"Reply *yes* to save or *no* to cancel."
    )


def _send_bill_async(client_id: str, from_number: str,
                      record: dict, session: dict) -> None:
    """Send bill in background thread."""
    import threading
    def _send():
        try:
            from services.database import get_db
            from utils.bill_generator import generate_and_send_bill
            db_client = get_db()
            client_rec = db_client.table("clients").select("name, business_name") \
                .eq("id", client_id).limit(1).execute()
            business_name = "My Store"
            if client_rec.data:
                business_name = (client_rec.data[0].get("business_name")
                                 or client_rec.data[0].get("name", "My Store"))
            # Customer phone: session first, then DB
            customer_phone = session.get("_customer_phone", "")
            if not customer_phone:
                cust = db_client.table("customers").select("phone") \
                    .eq("client_id", client_id) \
                    .ilike("name", record["customer_name"]) \
                    .limit(1).execute()
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
            logger.error(f"Bill send error: {e}")

    threading.Thread(target=_send, daemon=True).start()


def _send_reminders_async(client_id: str) -> None:
    """Send due reminders to all customers with pending dues."""
    import threading
    def _send():
        try:
            from services.database import get_db, get_overdue_customers
            from services.evolution_service import send_text_message
            db_client = get_db()
            client_rec = db_client.table("clients").select("business_name, name") \
                .eq("id", client_id).limit(1).execute()
            shop = "our shop"
            if client_rec.data:
                shop = (client_rec.data[0].get("business_name")
                        or client_rec.data[0].get("name", "our shop"))

            overdue = get_overdue_customers(client_id, min_due=1)
            sent = 0
            for c in overdue:
                if c.get("phone"):
                    msg = (
                        f"Namaste *{c['name']}*! 🙏\n\n"
                        f"You have a pending due of *₹{c['total_due']:.0f}*"
                        f" at *{shop}*.\n\n"
                        f"Please clear at your earliest. 🙏\n"
                        f"_Digital Khata_"
                    )
                    send_text_message(c["phone"], msg, delay=2.5)
                    sent += 1
            logger.info(f"Reminders sent: {sent}/{len(overdue)}")
        except Exception as e:
            logger.error(f"Reminder error: {e}")

    threading.Thread(target=_send, daemon=True).start()
