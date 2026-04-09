"""
utils/calculator.py - Parse natural-language item/amount strings

Supports:
  loki 10*10         → qty=10, rate=10, amount=100
  chuchu 20*50       → qty=20, rate=50, amount=1000
  khir 90+10         → amount=100 (addition, no qty/rate split)
  milk 20            → qty=1, rate=20, amount=20
  rice 50 dal 30     → multiple items
  payment 50         → payment keyword
"""
from __future__ import annotations
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PAYMENT_KW = re.compile(r'\b(payment|paid|diya|pay|jama)\b', re.IGNORECASE)
_DUE_KW     = re.compile(r'\b(due|baaki|baki|udhar|bcha)\b', re.IGNORECASE)

# Matches: word followed by number*number (qty*rate)
_QTY_RATE_RE = re.compile(
    r'([a-zA-Z\u0900-\u097F]+)\s+'   # item name
    r'(\d+(?:\.\d+)?)'               # first number (qty)
    r'\s*[xX×\*]\s*'                 # separator: * x × X
    r'(\d+(?:\.\d+)?)',              # second number (rate)
    re.UNICODE
)

# Matches: word followed by single number or expression
_ITEM_AMOUNT_RE = re.compile(
    r'([a-zA-Z\u0900-\u097F]+)\s+'
    r'([\d]+(?:\.\d+)?(?:\s*[\+\-\/]\s*[\d]+(?:\.\d+)?)*)',
    re.UNICODE
)


def eval_amount(expr: str) -> float:
    """Safely evaluate a math expression. + - / only (not *)"""
    if not expr:
        return 0.0
    expr = str(expr).strip()
    # Normalize spaces around operators
    expr = re.sub(r'\s*([\+\-\/])\s*', r'\1', expr)
    # Only allow safe characters (no * here — * means qty*rate)
    if re.fullmatch(r'[\d\.\+\-\/]+', expr):
        try:
            return max(0.0, round(float(eval(expr)), 2))  # noqa: S307
        except Exception:
            pass
    try:
        return float(expr)
    except ValueError:
        return 0.0


def parse_items_text(text: str) -> tuple[list[dict], float]:
    """
    Parse free-form item text into structured list.

    Handles:
      "loki 10*10 chuchu 20*50"  → qty/rate split
      "milk 20 bread 15"         → simple amounts
      "khir 90+10"               → expression amount
    """
    items: list[dict] = []
    total = 0.0

    # Remove payment keywords
    clean = re.sub(
        r'\b(?:payment|paid|diya|pay|jama)\s+[\d\.\+\-\*x×\/\s]+',
        '', text, flags=re.IGNORECASE
    ).strip()

    # Step 1 — Extract all qty*rate patterns first
    qty_rate_matches = []
    for m in _QTY_RATE_RE.finditer(clean):
        qty_rate_matches.append((m.start(), m.end(), m.group(1), m.group(2), m.group(3)))

    if qty_rate_matches:
        # Process qty*rate matches
        processed_spans = []
        for start, end, name, qty_s, rate_s in qty_rate_matches:
            qty    = float(qty_s)
            rate   = float(rate_s)
            amount = round(qty * rate, 2)
            items.append({
                "name":   name.capitalize(),
                "qty":    qty,
                "rate":   rate,
                "amount": amount,
            })
            total += amount
            processed_spans.append((start, end))

        # Now find remaining text not covered by qty*rate matches
        remaining = clean
        # Remove all qty*rate patterns from remaining
        remaining = _QTY_RATE_RE.sub('', remaining).strip()

        # Parse remaining as simple amounts
        if remaining:
            extra_items, extra_total = _parse_simple_amounts(remaining)
            items.extend(extra_items)
            total += extra_total
    else:
        # No qty*rate patterns — parse as simple amounts
        items, total = _parse_simple_amounts(clean)

    return items, round(total, 2)


def _parse_simple_amounts(text: str) -> tuple[list[dict], float]:
    """Parse 'item amount' pairs without qty*rate."""
    items = []
    total = 0.0

    matches = list(_ITEM_AMOUNT_RE.finditer(text))
    for m in matches:
        name       = m.group(1).strip().capitalize()
        amount_str = m.group(2).strip()

        # Skip payment keywords
        if _PAYMENT_KW.match(name) or _DUE_KW.match(name):
            continue

        amount = eval_amount(amount_str)
        if amount > 0 and name:
            items.append({
                "name":   name,
                "qty":    1,
                "rate":   amount,
                "amount": amount,
            })
            total += amount

    return items, round(total, 2)


def parse_voice_message(text: str) -> dict:
    """
    Parse a full voice/text message.
    First word = customer name, rest = items + payment.

    Examples:
      "Rahul loki 10*10 chuchu 20*50 payment 50"
      "Rahul milk 20 bread 15"
    """
    result = {
        "customer_name": None,
        "items": [],
        "current_total": 0.0,
        "payment": 0.0,
        "command": None,
    }

    text = text.strip()
    if not text:
        return result

    lower = text.lower()
    for cmd in ("history", "due", "undo", "delete"):
        if lower.startswith(cmd):
            result["command"] = cmd
            rest = text[len(cmd):].strip().split()
            if rest:
                result["customer_name"] = rest[0].title()
            return result

    tokens = text.split()
    if not tokens:
        return result

    result["customer_name"] = tokens[0].title()
    remaining = " ".join(tokens[1:])

    # Extract payment
    pay_match = re.search(
        r'\b(?:payment|paid|diya|pay|jama)\s+([\d\.\+\-\*x×\/\s]+?)(?:\s+[a-zA-Z]|$)',
        remaining, re.IGNORECASE
    )
    if pay_match:
        pay_str = re.sub(r'[xX×\*]', '*', pay_match.group(1).strip())
        try:
            result["payment"] = max(0.0, float(eval(pay_str)))  # noqa
        except Exception:
            result["payment"] = eval_amount(pay_str)
        remaining = remaining[:pay_match.start()] + remaining[pay_match.end():]

    items, total = parse_items_text(remaining)
    result["items"]         = items
    result["current_total"] = total

    return result


def format_items_list(items: list[dict]) -> str:
    """Format items for WhatsApp confirmation message."""
    if not items:
        return "  —"
    lines = []
    for it in items:
        qty  = it.get("qty", 1)
        rate = it.get("rate", it.get("amount", 0))
        amt  = it.get("amount", 0)
        name = it.get("name", "")
        if qty > 1 and rate != amt:
            lines.append(f"  • {name}: {qty:.0f} × {rate:.0f} = ₹{amt:.0f}")
        else:
            lines.append(f"  • {name}: ₹{amt:.0f}")
    return "\n".join(lines)
