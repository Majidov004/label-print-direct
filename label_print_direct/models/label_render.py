# -*- coding: utf-8 -*-
"""Təmiz render köməkçiləri (ORM-siz).

spec  = {"width_mm", "height_mm", "dpi", "elements": [ {...element dict...} ]}
data  = {"name", "name_plain", "code", "barcode", "price_amount", "currency", "fields": {}}

spec + data  ->  1-bit PIL şəkli  ->  TSPL BITMAP əmri (printerə gedən baytlar)
Eyni funksiyalar dizaynerin canlı önizləməsini (PNG) də verir, ona görə
ekranda görünən çapla birəbir eynidir.
"""
import base64
import io
import logging
import os
import re

_logger = logging.getLogger(__name__)

# Sabit mətndə yer tutucular: {name} {name_plain} {code} {barcode} {price} {price_amount}
# {currency} və {product.sahə} {doc.sahə} {line.sahə}
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\.([a-zA-Z_][a-zA-Z0-9_]*))?\}")
BASE_PLACEHOLDERS = ("name", "name_plain", "code", "barcode", "price", "price_amount", "currency")


def text_placeholders(text):
    """Mətndəki {mənbə.sahə} yer tutucuları: [(source, field)]."""
    out = []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        if m.group(2) and m.group(1) in ("product", "doc", "line"):
            out.append((m.group(1), m.group(2)))
    return out


def substitute(text, data):
    """Yer tutucuları real dəyərlərlə əvəz edir; tanınmayanlar olduğu kimi qalır."""
    def repl(m):
        a, b = m.group(1), m.group(2)
        if b:
            return (data.get("fields") or {}).get(a + ":" + b, "") or ""
        if a == "price":
            return ((data.get("price_amount") or "") + " " + (data.get("currency") or "")).strip()
        if a in BASE_PLACEHOLDERS:
            return data.get(a) or ""
        return m.group(0)
    return PLACEHOLDER_RE.sub(repl, text or "")

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_CANDIDATES = [
    os.path.join(MODULE_DIR, "static", "fonts", "NotoSans-Regular.ttf"),
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_CACHE = {}

TEXT_TYPES = ("name", "name_plain", "code", "barcode_text", "price", "currency", "text", "field")

SAMPLE_DATA = {
    "name": "[REF-001] Nümunə məhsul adı",
    "name_plain": "Nümunə məhsul adı",
    "code": "REF-001",
    "barcode": "2000000000002",
    "price_amount": "12.50",
    "currency": "₼",
    "fields": {},
}


def load_font(px):
    from PIL import ImageFont
    px = max(4, int(px))
    font = _FONT_CACHE.get(px)
    if font is not None:
        return font
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, px)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=px)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[px] = font
    return font


def mm_to_dots(mm, dpi):
    return int(round(float(mm or 0.0) * float(dpi or 203) / 25.4))


def fmt_num(v):
    v = float(v or 0)
    return str(int(v)) if v == int(v) else ("%g" % v)


# --------------------------------------------------------------------- #
#  Barkodlar: [(is_bar, modul_sayı), ...]
# --------------------------------------------------------------------- #
def code128_bars(text):
    """reportlab-ın Code128 kodlayıcısı: rəqəmlər üçün C dəsti (sıx), qalanı B."""
    from reportlab.graphics.barcode import code128
    bc = code128.Code128(text or "0")
    bc.validate()
    bc.encode()
    out = []
    for ch in bc.decompose():           # A..D = zolaq, a..d = boşluq (en 1..4 modul)
        if ch.isupper():
            out.append((True, ord(ch) - ord("A") + 1))
        else:
            out.append((False, ord(ch) - ord("a") + 1))
    return out


_EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
          "0110001", "0101111", "0111011", "0110111", "0001011"]
_EAN_G = ["0100111", "0110011", "0011011", "0100001", "0011101",
          "0111001", "0000101", "0010001", "0001001", "0010111"]
_EAN_R = ["1110010", "1100110", "1101100", "1000010", "1011100",
          "1001110", "1010000", "1000100", "1001000", "1110100"]
_EAN_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
               "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def ean13_check_digit(d12):
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d12))
    return str((10 - s % 10) % 10)


def ean13_bars(text):
    digits = "".join(c for c in (text or "") if c.isdigit())
    if len(digits) == 12:
        digits += ean13_check_digit(digits)
    if len(digits) != 13:
        return None
    parity = _EAN_PARITY[int(digits[0])]
    bits = "101"
    for i, c in enumerate(digits[1:7]):
        bits += (_EAN_L if parity[i] == "L" else _EAN_G)[int(c)]
    bits += "01010"
    for c in digits[7:]:
        bits += _EAN_R[int(c)]
    bits += "101"
    out = []
    for b in bits:
        is_bar = b == "1"
        if out and out[-1][0] == is_bar:
            out[-1] = (is_bar, out[-1][1] + 1)
        else:
            out.append((is_bar, 1))
    return out


def barcode_bars(content, symbology):
    if symbology == "ean13":
        bars = ean13_bars(content)
        if bars:
            return bars
    return code128_bars(content)


def draw_bars(draw, x, y, width, height, bars):
    total = sum(m for _, m in bars) or 1
    module = float(width) / float(total)
    xf = float(x)
    for is_bar, m in bars:
        w = m * module
        if is_bar:
            x0 = int(round(xf))
            x1 = max(x0, int(round(xf + w)) - 1)
            draw.rectangle([x0, y, x1, y + height - 1], fill=0)
        xf += w


# --------------------------------------------------------------------- #
#  Mətn
# --------------------------------------------------------------------- #
def element_text(el, data):
    t = el.get("type")
    if t == "name":
        return data.get("name") or ""
    if t == "name_plain":
        return data.get("name_plain") or ""
    if t == "code":
        return data.get("code") or ""
    if t == "barcode_text":
        return data.get("barcode") or ""
    if t == "price":
        amount = data.get("price_amount") or ""
        if el.get("price_amount_only"):
            return amount
        return (amount + " " + (data.get("currency") or "")).strip()
    if t == "currency":
        return data.get("currency") or ""
    if t == "text":
        return substitute(el.get("text") or "", data)
    if t == "field":
        key = (el.get("field_source") or "product") + ":" + (el.get("field_name") or "")
        val = (data.get("fields") or {}).get(key, "") or ""
        if el.get("short_name") and " / " in val:        # «All / İçki / Su» → «Su»
            val = val.rsplit(" / ", 1)[-1]
        return val
    return ""


def field_key(el):
    """«Sahə» elementinin data["fields"] lüğətindəki açarı: mənbə:ad."""
    return (el.get("field_source") or "product") + ":" + (el.get("field_name") or "")


def _shorten(measure, text, max_w):
    if measure(text) <= max_w:
        return text
    t = text
    while t and measure(t + "…") > max_w:
        t = t[:-1]
    return (t + "…") if t else "…"


def wrap_lines(measure, text, max_w, max_lines):
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if not cur or measure(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                cur = ""
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if not lines:
        lines = [""]
    truncated = " ".join(lines).strip() != (text or "").strip()
    lines = [_shorten(measure, ln, max_w) for ln in lines]
    if truncated and not lines[-1].endswith("…"):
        lines[-1] = _shorten(measure, lines[-1] + "…", max_w)
    return lines


def _text_block(el, text, dpi, label_w_dots, measure_draw):
    """Element mətnini (fırlanmadan əvvəl) ayrıca 1-bit şəklə çəkir."""
    from PIL import Image, ImageDraw
    px = max(4, int(round(float(el.get("font_pt") or 9) * dpi / 72.0)))
    font = load_font(px)
    sw = 1 if el.get("bold") else 0
    x = mm_to_dots(el.get("x_mm"), dpi)
    box_w = mm_to_dots(el.get("width_mm"), dpi) if el.get("width_mm") else max(8, label_w_dots - x)
    box_w = max(8, box_w)

    def measure(s):
        return measure_draw.textlength(s, font=font)

    if el.get("wrap"):
        lines = wrap_lines(measure, text, box_w, max(1, int(el.get("wrap_lines") or 2)))
    else:
        lines = [text]
    lh = max(px + 1, int(round(px * 1.2)))
    block = Image.new("1", (box_w + 2, lh * len(lines) + 2), 1)
    d = ImageDraw.Draw(block)
    align = el.get("align") or "left"
    for i, ln in enumerate(lines):
        yy = i * lh
        if align == "center":
            d.text((box_w // 2, yy), ln, font=font, fill=0, anchor="ma",
                   stroke_width=sw, stroke_fill=0)
        elif align == "right":
            d.text((box_w, yy), ln, font=font, fill=0, anchor="ra",
                   stroke_width=sw, stroke_fill=0)
        else:
            d.text((0, yy), ln, font=font, fill=0, anchor="la",
                   stroke_width=sw, stroke_fill=0)
    return block


def _barcode_block(el, content, dpi, measure_draw):
    from PIL import Image, ImageDraw
    bars = barcode_bars(content, el.get("barcode_symbology") or "code128")
    modules = sum(m for _, m in bars) or 1
    if el.get("width_mm"):
        width = max(modules, mm_to_dots(el.get("width_mm"), dpi))
    else:
        width = modules * max(1, int(el.get("module_width") or 2))
    bar_h = max(4, mm_to_dots(el.get("height_mm") or 10, dpi))
    text_h = 0
    font = None
    if el.get("show_barcode_text"):
        px = max(4, int(round(float(el.get("font_pt") or 7) * dpi / 72.0)))
        font = load_font(px)
        text_h = int(round(px * 1.25)) + 1
    block = Image.new("1", (width + 1, bar_h + text_h + 1), 1)
    d = ImageDraw.Draw(block)
    draw_bars(d, 0, 0, width, bar_h, bars)
    if font is not None:
        d.text((width // 2, bar_h + 1), content, font=font, fill=0, anchor="ma")
    return block


def _shape_block(el, dpi):
    from PIL import Image, ImageDraw
    w = max(1, mm_to_dots(el.get("width_mm") or 10, dpi))
    h = max(1, mm_to_dots(el.get("height_mm") or 0.4, dpi))
    thick = max(1, mm_to_dots(el.get("thickness_mm") or 0.3, dpi))
    block = Image.new("1", (w, h), 1)
    d = ImageDraw.Draw(block)
    if el.get("type") == "rect":
        d.rectangle([0, 0, w - 1, h - 1], outline=0, width=thick)
    else:
        d.rectangle([0, 0, w - 1, h - 1], fill=0)
    return block


def render_element(el, data, dpi, label_w_dots, measure_draw):
    t = el.get("type")
    if t in ("line", "rect"):
        return _shape_block(el, dpi)
    if t == "barcode":
        content = (data.get("barcode") or "").strip()
        if not content:
            return None
        return _barcode_block(el, content, dpi, measure_draw)
    if t in TEXT_TYPES:
        text = str(element_text(el, data) or "").strip()
        if not text:
            return None
        return _text_block(el, text, dpi, label_w_dots, measure_draw)
    return None


# --------------------------------------------------------------------- #
#  Bütöv etiket
# --------------------------------------------------------------------- #
def render_label(spec, data):
    """spec + data -> 1-bit PIL şəkli (0 = qara, 1 = ağ)."""
    from PIL import Image, ImageDraw, ImageOps
    dpi = int(spec.get("dpi") or 203)
    W = max(8, mm_to_dots(spec.get("width_mm") or 40, dpi))
    H = max(8, mm_to_dots(spec.get("height_mm") or 30, dpi))
    img = Image.new("1", (W, H), 1)
    draw = ImageDraw.Draw(img)
    elements = sorted(spec.get("elements") or [], key=lambda e: (e.get("sequence") or 0))
    for el in elements:
        try:
            block = render_element(el, data, dpi, W, draw)
            if block is None:
                continue
            rot = int(el.get("rotation") or 0) % 360
            if rot:
                block = block.rotate(-rot, expand=True, fillcolor=1)   # saat istiqamətində
            mask = ImageOps.invert(block.convert("L")).convert("1")
            img.paste(0, (mm_to_dots(el.get("x_mm"), dpi), mm_to_dots(el.get("y_mm"), dpi)), mask)
        except Exception as e:                       # bir element bütün etiketi pozmasın
            _logger.warning("Etiket elementi çəkilmədi (%s): %s", el.get("type"), e)
    return img


def tspl_bitmap_payload(spec, img, copies=1):
    """1-bit şəkli TSPL BITMAP əmrinə bükür (printerə gedən xam baytlar)."""
    from PIL import Image
    W, H = img.size
    wb = (W + 7) // 8
    padded = Image.new("1", (wb * 8, H), 1)
    padded.paste(img, (0, 0))
    data = padded.tobytes()                          # 1 bit = ağ (nöqtə yoxdur), MSB əvvəl
    gap = spec.get("gap_mm")
    header = (
        "SIZE %s mm,%s mm\r\nGAP %s mm,0 mm\r\nDIRECTION %s\r\nDENSITY %d\r\nSPEED %d\r\n"
        "CLS\r\nBITMAP 0,0,%d,%d,0,"
        % (fmt_num(spec.get("width_mm")), fmt_num(spec.get("height_mm")),
           fmt_num(gap if gap is not None else 2),
           str(spec.get("direction") or "1"),
           max(0, min(15, int(spec.get("density") or 8))),
           max(1, min(12, int(spec.get("speed") or 3))), wb, H)
    ).encode("latin-1")
    footer = ("\r\nPRINT %d,1\r\n" % max(1, int(copies or 1))).encode("latin-1")
    return header + data + footer


def escpos_raster_payload(img, copies=1):
    """1-bit şəkli ESC/POS raster (GS v 0) əmrinə bükür — çek printerləri üçün.
    TSPL-dən fərqli olaraq burada 1 bit = qara."""
    from PIL import Image
    W, H = img.size
    wb = (W + 7) // 8
    padded = Image.new("1", (wb * 8, H), 1)
    padded.paste(img, (0, 0))
    data = bytes((~b) & 0xFF for b in padded.tobytes())
    one = (b"\x1b@" + b"\x1ba\x00"                                    # init, sola düz
           + b"\x1dv0\x00" + bytes([wb & 0xFF, wb >> 8, H & 0xFF, H >> 8]) + data
           + b"\x1bd\x03" + b"\x1dV\x42\x00")                         # boşluq, kəs
    return one * max(1, int(copies or 1))


def payload_b64(raw):
    return "#B64#" + base64.b64encode(raw).decode("ascii")


def png_data_url(img, scale=2):
    from PIL import Image
    big = img.convert("L")
    if scale and scale != 1:
        big = big.resize((img.size[0] * scale, img.size[1] * scale), Image.NEAREST)
    buf = io.BytesIO()
    big.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
