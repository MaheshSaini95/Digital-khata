"""
utils/bill_generator.py
Matches Shri Shyam Vegetable Company bill format exactly.
Items format: "tamatar 25*25" → name=tamatar, qty=25, rate=25, amount=625
"""
from __future__ import annotations
import os, io, base64, logging, tempfile, requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Colors ────────────────────────────────────────────────────
WHITE      = (255, 255, 255)
BLACK      = (15,  15,  15)
PINK_BG    = (255, 220, 220)   # like the pink bill paper
DARK_BLUE  = (10,  30,  100)
RED        = (180, 0,   0)
GRAY       = (100, 100, 100)
LIGHT_GRAY = (240, 240, 240)
TABLE_LINE = (150, 150, 180)
GREEN      = (0,   120, 0)
W = 794  # A4-ish width


def _font(size, bold=False):
    candidates = []
    if bold:
        candidates = [
            "C:/Windows/Fonts/Arialbd.ttf",
            "C:/Windows/Fonts/Calibrib.ttf",
            "C:/Windows/Fonts/Verdanab.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/Arial.ttf",
            "C:/Windows/Fonts/Calibri.ttf",
            "C:/Windows/Fonts/Verdana.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _cx(draw, text, y, font, color, w=W):
    """Center text."""
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text(((w - (bb[2]-bb[0])) // 2, y), text, font=font, fill=color)


def _line(draw, y, x1=20, x2=None, color=TABLE_LINE, width=1):
    draw.line([(x1, y), (x2 or W-20, y)], fill=color, width=width)


def parse_item_entry(item: dict) -> tuple[str, float, float, float]:
    """
    Extract name, qty, rate, amount from item dict.
    item may have:
      - name, amount (simple: treat as amount directly, qty=1, rate=amount)
      - name, qty, rate, amount (full)
    """
    name   = item.get("name", "Item")
    amount = float(item.get("amount", 0))
    qty    = float(item.get("qty", 0))
    rate   = float(item.get("rate", 0))

    if qty > 0 and rate > 0:
        amount = round(qty * rate, 2)
    elif qty > 0 and amount > 0:
        rate = round(amount / qty, 2)
    elif rate > 0 and amount > 0:
        qty = round(amount / rate, 2)
    else:
        # Simple entry — just amount
        qty = 1
        rate = amount

    return name, qty, rate, amount


def create_bill_image(
    business_name: str,
    owner_name: str,
    owner_phone: str,
    address: str,
    customer_name: str,
    bill_no: str,
    items: list[dict],
    current_total: float,
    payment: float,
    previous_due: float,
    updated_due: float,
    record_date: str = None,
) -> str:
    """Generate bill image matching the pink Shri Shyam format."""

    date_str = record_date or datetime.now().strftime("%d/%m/%y")

    # Dynamic height
    row_h = 28
    H = 380 + len(items) * row_h + 120
    H = max(H, 700)

    img  = Image.new("RGB", (W, H), PINK_BG)
    draw = ImageDraw.Draw(img)

    # ── Fonts ──────────────────────────────────────────────────
    f_shop   = _font(22, bold=True)
    f_tag    = _font(13, bold=True)
    f_addr   = _font(11)
    f_owner  = _font(12)
    f_label  = _font(13, bold=True)
    f_data   = _font(13)
    f_thead  = _font(12, bold=True)
    f_tdata  = _font(12)
    f_total  = _font(14, bold=True)
    f_due    = _font(16, bold=True)
    f_small  = _font(10)

    y = 16

    # ── Owner line ────────────────────────────────────────────
    draw.text((20, y), f"!! Shri !!   {owner_name}   {owner_phone}", font=f_owner, fill=DARK_BLUE)
    y += 20

    # ── Shop name ─────────────────────────────────────────────
    _cx(draw, business_name.upper(), y, f_shop, DARK_BLUE)
    y += 30

    # Tagline box
    tag = "Vegetable and Fruit At Your Home"
    tb  = draw.textbbox((0,0), tag, font=f_tag)
    tw  = tb[2]-tb[0]
    tx  = (W-tw)//2
    draw.rectangle([tx-8, y, tx+tw+8, y+20], outline=DARK_BLUE, width=1)
    draw.text((tx, y+2), tag, font=f_tag, fill=DARK_BLUE)
    y += 26

    # Address
    _cx(draw, address, y, f_addr, DARK_BLUE)
    y += 18

    _line(draw, y, width=2, color=DARK_BLUE)
    y += 8

    # ── Bill No + Date + Customer ─────────────────────────────
    draw.text((20, y), "Bill No.", font=f_label, fill=DARK_BLUE)
    draw.text((90, y), bill_no, font=f_label, fill=RED)
    draw.text((W-160, y), f"Date: {date_str}", font=f_label, fill=DARK_BLUE)
    y += 22

    draw.text((20, y), "M/s.", font=f_label, fill=DARK_BLUE)
    draw.text((60, y), customer_name.upper(), font=f_label, fill=RED)
    y += 20

    _line(draw, y, width=1, color=DARK_BLUE)
    y += 6

    # ── Table header ──────────────────────────────────────────
    # Columns: Sr | Particulars | Qty | Rate | Amount
    COL = {"sr":20, "item":60, "qty":500, "rate":590, "amt":690}

    draw.rectangle([20, y, W-20, y+24], fill=DARK_BLUE)
    draw.text((COL["sr"]+2,   y+4), "Sr.",          font=f_thead, fill=WHITE)
    draw.text((COL["item"],   y+4), "PARTICULARS",  font=f_thead, fill=WHITE)
    draw.text((COL["qty"],    y+4), "Qty.",          font=f_thead, fill=WHITE)
    draw.text((COL["rate"],   y+4), "Rate",          font=f_thead, fill=WHITE)
    draw.text((COL["amt"],    y+4), "Amount",        font=f_thead, fill=WHITE)
    y += 24

    WHITE_ROW = (255, 240, 240)
    PINK_ROW  = (255, 210, 210)

    total_calc = 0.0

    for i, item in enumerate(items):
        name, qty, rate, amount = parse_item_entry(item)
        total_calc += amount
        row_bg = WHITE_ROW if i % 2 == 0 else PINK_ROW
        draw.rectangle([20, y, W-20, y+row_h-1], fill=row_bg)

        draw.text((COL["sr"]+2, y+6),  str(i+1),          font=f_tdata, fill=BLACK)
        draw.text((COL["item"],  y+6),  name.capitalize(), font=f_tdata, fill=BLACK)

        qty_s  = f"{qty:.0f}" if qty == int(qty) else f"{qty:.1f}"
        rate_s = f"{rate:.0f}" if rate == int(rate) else f"{rate:.1f}"
        amt_s  = f"{amount:.0f}"

        draw.text((COL["qty"],  y+6), qty_s,  font=f_tdata, fill=BLACK)
        draw.text((COL["rate"], y+6), rate_s, font=f_tdata, fill=BLACK)

        # Right-align amount
        ab = draw.textbbox((0,0), amt_s, font=f_tdata)
        draw.text((W-22-(ab[2]-ab[0]), y+6), amt_s, font=f_tdata, fill=BLACK)

        # Row bottom line
        _line(draw, y+row_h-1, color=TABLE_LINE)
        y += row_h

    # Vertical lines for table
    for col_x in [COL["qty"]-6, COL["rate"]-6, COL["amt"]-6]:
        draw.line([(col_x, y-len(items)*row_h-24), (col_x, y)], fill=TABLE_LINE, width=1)

    _line(draw, y, width=2, color=DARK_BLUE)
    y += 8

    # ── Totals section ────────────────────────────────────────
    def right_row(label, value, fy=f_label, color=BLACK):
        nonlocal y
        draw.text((350, y), label, font=fy, fill=GRAY)
        vs = f"Rs. {value:.0f}"
        vb = draw.textbbox((0,0), vs, font=fy)
        draw.text((W-22-(vb[2]-vb[0]), y), vs, font=fy, fill=color)
        y += 22

    right_row("TOTAL",       current_total, color=DARK_BLUE)
    right_row("Pichla Bakaya (Prev Due)", previous_due, color=RED)
    right_row("Payment Received", payment, color=GREEN)

    _line(draw, y, x1=350, width=2, color=DARK_BLUE)
    y += 6

    # Total Amount box
    draw.rectangle([350, y, W-20, y+32], fill=DARK_BLUE)
    draw.text((356, y+6), "TOTAL AMOUNT", font=f_total, fill=WHITE)
    ts = f"Rs. {updated_due:.0f}"
    tb2 = draw.textbbox((0,0), ts, font=f_total)
    draw.text((W-22-(tb2[2]-tb2[0]), y+6), ts, font=f_total, fill=WHITE)
    y += 40

    # ── Footer ────────────────────────────────────────────────
    _line(draw, y, width=1, color=DARK_BLUE)
    y += 8
    draw.text((20, y),   "1. All Subject to Jaipur Jurisdiction only.", font=f_small, fill=GRAY)
    draw.text((20, y+14),"2. E.&O.E.",                                   font=f_small, fill=GRAY)

    fr = f"For : {business_name}"
    fb = draw.textbbox((0,0), fr, font=f_label)
    draw.text((W-22-(fb[2]-fb[0]), y), fr, font=f_label, fill=DARK_BLUE)
    y += 30

    # Powered by
    _cx(draw, "Generated by Digital Khata", y, f_small, GRAY)

    # ── Save ──────────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.save(tmp.name, "PNG", dpi=(150, 150))
    tmp.close()
    logger.info(f"Bill saved: {tmp.name}")
    return tmp.name


def upload_to_imgbb(image_path: str):
    from config import Config
    api_key = getattr(Config, "IMGBB_API_KEY", "") or os.getenv("IMGBB_API_KEY", "")
    if not api_key:
        return None
    try:
        with open(image_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        r = requests.post("https://api.imgbb.com/1/upload",
                          data={"key": api_key, "image": data, "expiration": 600},
                          timeout=30)
        res = r.json()
        if res.get("success"):
            url = res["data"]["url"]
            logger.info(f"Uploaded to imgbb: {url}")
            return url
        logger.error(f"imgbb failed: {res}")
    except Exception as e:
        logger.error(f"imgbb error: {e}")
    return None


def _build_text_bill(business_name, customer_name, items,
                     current_total, payment, previous_due,
                     updated_due, record_date):
    date_str = record_date or datetime.now().strftime("%d-%m-%Y")
    rows = []
    for i, item in enumerate(items, 1):
        name, qty, rate, amount = parse_item_entry(item)
        rows.append(
            f"  {i}. {name.capitalize():<15} {qty:>4.0f} x {rate:>6.0f} = Rs.{amount:.0f}"
        )
    items_text = "\n".join(rows) if rows else "  Payment only"

    due_emoji = "🔴" if updated_due > 0 else "🟢"
    return (
        f"🧾 *{business_name.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {customer_name}   📅 {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*Sr  Item            Qty  Rate   Amt*\n"
        f"{items_text}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Total:      Rs.{current_total:.0f}\n"
        f"📊 Prev Due:   Rs.{previous_due:.0f}\n"
        f"✅ Payment:    Rs.{payment:.0f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{due_emoji} *Due: Rs.{updated_due:.0f}*\n"
        f"_Digital Khata_"
    )


def send_bill_via_whatsapp(to_number, bill_path, customer_name,
                            business_name, updated_due, items,
                            current_total, payment, previous_due,
                            record_date) -> bool:
    try:
        from config import Config
        from twilio.rest import Client
        client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)

        if not to_number.startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"

        image_url = upload_to_imgbb(bill_path)
        text = _build_text_bill(business_name, customer_name, items,
                                 current_total, payment, previous_due,
                                 updated_due, record_date)

        if image_url:
            msg = client.messages.create(
                from_=Config.TWILIO_WHATSAPP_FROM,
                to=to_number,
                body=f"🧾 Bill - {business_name}\nDue: Rs.{updated_due:.0f}",
                media_url=[image_url],
            )
            logger.info(f"Bill IMAGE sent to {to_number}: {msg.sid}")
        else:
            msg = client.messages.create(
                from_=Config.TWILIO_WHATSAPP_FROM,
                to=to_number,
                body=text,
            )
            logger.info(f"Bill TEXT sent to {to_number}: {msg.sid}")
        return True
    except Exception as e:
        logger.error(f"Bill send failed: {e}")
        return False


def generate_and_send_bill(business_name, customer_name, customer_phone,
                            items, current_total, payment, previous_due,
                            updated_due, record_date=None,
                            sender_number=None) -> str:
    """Generate bill image and send to customer + shop owner."""
    from config import Config

    # Get shop details from config or use defaults
    owner_name  = getattr(Config, "OWNER_NAME",  "") or "Shop Owner"
    owner_phone = getattr(Config, "OWNER_PHONE", "") or ""
    address     = getattr(Config, "SHOP_ADDRESS","") or "Jaipur, Rajasthan"
    bill_no     = str(int(datetime.now().timestamp()) % 10000)

    bill_path = create_bill_image(
        business_name=business_name,
        owner_name=owner_name,
        owner_phone=owner_phone,
        address=address,
        customer_name=customer_name,
        bill_no=bill_no,
        items=items,
        current_total=current_total,
        payment=payment,
        previous_due=previous_due,
        updated_due=updated_due,
        record_date=record_date,
    )

    kwargs = dict(
        bill_path=bill_path,
        customer_name=customer_name,
        business_name=business_name,
        updated_due=updated_due,
        items=items,
        current_total=current_total,
        payment=payment,
        previous_due=previous_due,
        record_date=record_date,
    )

    # Send to customer
    if customer_phone:
        send_bill_via_whatsapp(to_number=customer_phone, **kwargs)
    else:
        logger.warning(f"No phone for {customer_name} — bill not sent to customer")

    # Send to shop owner
    if sender_number:
        send_bill_via_whatsapp(to_number=sender_number, **kwargs)

    try:
        os.unlink(bill_path)
    except Exception:
        pass

    return bill_path
