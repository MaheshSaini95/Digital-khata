"""
routes/api.py - REST API for React admin dashboard
JWT-based authentication.
"""
from __future__ import annotations
import logging
import io
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from functools import wraps
import jwt
from config import Config
import services.database as db
from utils.pdf_generator import generate_customer_statement, generate_monthly_report

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ── Auth helpers ──────────────────────────────────────────

def _make_token(client_id: str) -> str:
    import time
    payload = {"sub": client_id, "iat": int(time.time()), "exp": int(time.time()) + 86400}
    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def _decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        payload = _decode_token(token)
        if not payload:
            return jsonify({"error": "Unauthorized"}), 401
        request.client_id = payload["sub"]
        return f(*args, **kwargs)
    return decorated


# ── Auth endpoints ─────────────────────────────────────────

@api_bp.post("/auth/login")
def login():
    """
    Simple login using WhatsApp number + business key.
    In production, replace with OTP or proper auth.
    """
    data = request.json or {}
    number = data.get("whatsapp_number", "")
    client = db.get_client_by_number(number)
    if not client:
        return jsonify({"error": "Account not found"}), 404

    token = _make_token(client["id"])
    return jsonify({"token": token, "client": client})


@api_bp.post("/auth/register")
def register():
    data = request.json or {}
    required = ["name", "whatsapp_number"]
    if not all(k in data for k in required):
        return jsonify({"error": "name and whatsapp_number required"}), 400
    try:
        client = db.upsert_client(
            name=data["name"],
            whatsapp_number=data["whatsapp_number"],
            business_name=data.get("business_name", ""),
        )
        token = _make_token(client["id"])
        return jsonify({"token": token, "client": client}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard summary ──────────────────────────────────────

@api_bp.get("/dashboard/summary")
@require_auth
def dashboard_summary():
    client_id = request.client_id
    customers = db.get_all_customers(client_id)
    total_due = sum(float(c.get("total_due", 0)) for c in customers)
    overdue = [c for c in customers if float(c.get("total_due", 0)) > 0]

    return jsonify({
        "total_customers": len(customers),
        "total_due": total_due,
        "customers_with_due": len(overdue),
        "top_debtors": sorted(overdue, key=lambda x: -x["total_due"])[:5],
    })


# ── Customers ──────────────────────────────────────────────

@api_bp.get("/customers")
@require_auth
def list_customers():
    customers = db.get_all_customers(request.client_id)
    return jsonify(customers)


@api_bp.get("/customers/search")
@require_auth
def search_customers():
    q = request.args.get("q", "")
    return jsonify(db.search_customers(request.client_id, q))


@api_bp.get("/customers/<name>/history")
@require_auth
def customer_history(name: str):
    limit = int(request.args.get("limit", 20))
    records = db.get_history(request.client_id, name, limit=limit)
    return jsonify(records)


@api_bp.get("/customers/<name>/due")
@require_auth
def customer_due(name: str):
    due = db.get_latest_due(request.client_id, name)
    if due is None:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify({"customer": name, "due": due})


# ── Records ────────────────────────────────────────────────

@api_bp.post("/records")
@require_auth
def create_record():
    data = request.json or {}
    try:
        record = db.add_record(
            client_id=request.client_id,
            customer_name=data["customer_name"],
            items=data.get("items", []),
            current_total=float(data.get("current_total", 0)),
            payment=float(data.get("payment", 0)),
            notes=data.get("notes", ""),
        )
        return jsonify(record), 201
    except KeyError as e:
        return jsonify({"error": f"Missing field: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.put("/records/<record_id>")
@require_auth
def update_record(record_id: str):
    data = request.json or {}
    try:
        record = db.update_record(
            record_id=record_id,
            client_id=request.client_id,
            items=data.get("items"),
            current_total=data.get("current_total"),
            payment=data.get("payment"),
            notes=data.get("notes"),
        )
        return jsonify(record)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.delete("/records/<record_id>")
@require_auth
def delete_record(record_id: str):
    ok = db.delete_record(record_id, request.client_id)
    if not ok:
        return jsonify({"error": "Record not found"}), 404
    return jsonify({"deleted": True})


# ── Reports ────────────────────────────────────────────────

@api_bp.get("/reports/monthly")
@require_auth
def monthly_report():
    year  = int(request.args.get("year",  datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    data  = db.get_monthly_summary(request.client_id, year, month)
    return jsonify({"year": year, "month": month, "summary": data})


@api_bp.get("/reports/overdue")
@require_auth
def overdue_report():
    min_due = float(request.args.get("min_due", 1))
    return jsonify(db.get_overdue_customers(request.client_id, min_due))


@api_bp.get("/reports/pdf/customer/<name>")
@require_auth
def pdf_customer_statement(name: str):
    from services.database import get_db
    db_client = get_db()
    client_rec = db_client.table("clients").select("name, business_name") \
        .eq("id", request.client_id).single().execute().data

    records = db.get_history(request.client_id, name, limit=100)
    pdf_bytes = generate_customer_statement(
        client_name=client_rec.get("name", ""),
        business_name=client_rec.get("business_name", ""),
        customer_name=name,
        records=records,
    )
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        download_name=f"statement_{name}_{datetime.now().strftime('%Y%m%d')}.pdf",
        as_attachment=True,
    )


@api_bp.get("/reports/pdf/monthly")
@require_auth
def pdf_monthly_report():
    year  = int(request.args.get("year",  datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    from services.database import get_db
    db_client = get_db()
    client_rec = db_client.table("clients").select("name, business_name") \
        .eq("id", request.client_id).single().execute().data

    summary = db.get_monthly_summary(request.client_id, year, month)
    import calendar
    month_label = f"{calendar.month_name[month]} {year}"
    pdf_bytes = generate_monthly_report(
        client_name=client_rec.get("name", ""),
        business_name=client_rec.get("business_name", ""),
        month_label=month_label,
        summary=summary,
    )
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        download_name=f"monthly_report_{year}_{month:02d}.pdf",
        as_attachment=True,
    )


# ── WhatsApp Reminders ─────────────────────────────────────

@api_bp.post("/reminders/send")
@require_auth
def send_reminders():
    """Send WhatsApp reminders to customers with pending dues."""
    from services.twilio_service import send_whatsapp_message
    from services.database import get_db

    db_client = get_db()
    client_rec = db_client.table("clients").select("*") \
        .eq("id", request.client_id).single().execute().data

    overdue = db.get_overdue_customers(request.client_id)
    sent = []
    for c in overdue:
        if c.get("phone"):
            msg = (
                f"Namaste *{c['name']}*! 🙏\n\n"
                f"You have a pending due of *₹{c['total_due']:.0f}* "
                f"at *{client_rec.get('business_name', 'our shop')}*.\n\n"
                "Please clear at your earliest convenience. 🙏"
            )
            try:
                send_whatsapp_message(c["phone"], msg)
                sent.append(c["name"])
            except Exception as e:
                logger.error(f"Reminder failed for {c['name']}: {e}")

    return jsonify({"sent": sent, "count": len(sent)})
