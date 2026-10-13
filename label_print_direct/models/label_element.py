# -*- coding: utf-8 -*-
"""Etiket üzərindəki bir element (ad, barkod, qiymət, mətn, xətt …).

Koordinatlar mm-lədir; (x, y) elementin etiket üzərindəki son çərçivəsinin
yuxarı-sol küncüdür (fırlanmadan sonra da). Dizayner bu modeli JSON kimi
oxuyub-yazır (bax: label.format.layout_json).
"""
from odoo import api, fields, models

ELEMENT_TYPES = [
    ("name", "Məhsul adı"),
    ("name_plain", "Məhsul adı (kodsuz)"),
    ("code", "Daxili kod"),
    ("barcode", "Barkod"),
    ("barcode_text", "Barkod nömrəsi"),
    ("price", "Qiymət"),
    ("currency", "Valyuta"),
    ("text", "Sabit mətn"),
    ("field", "Məhsul sahəsi"),
    ("line", "Xətt"),
    ("rect", "Çərçivə"),
]
ALIGN = [("left", "Sol"), ("center", "Orta"), ("right", "Sağ")]
ROTATION = [("0", "0°"), ("90", "90°"), ("180", "180°"), ("270", "270°")]


class LabelElement(models.Model):
    _name = "label.element"
    _description = "Etiket elementi"
    _order = "sequence, id"

    format_id = fields.Many2one("label.format", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    element_type = fields.Selection(ELEMENT_TYPES, string="Tip", required=True, default="text")

    x_mm = fields.Float(string="X (mm)", default=2.0)
    y_mm = fields.Float(string="Y (mm)", default=2.0)
    width_mm = fields.Float(string="Genişlik (mm)", default=0.0,
                            help="Mətn üçün qutu eni (0 = etiketin kənarına qədər); "
                                 "barkod, xətt və çərçivə üçün real en.")
    height_mm = fields.Float(string="Hündürlük (mm)", default=10.0,
                             help="Barkod zolaqlarının, xəttin və ya çərçivənin hündürlüyü.")

    font_pt = fields.Integer(string="Şrift (pt)", default=9)
    bold = fields.Boolean(string="Qalın")
    align = fields.Selection(ALIGN, string="Düzülüş", default="left", required=True)
    rotation = fields.Selection(ROTATION, string="Fırlanma", default="0", required=True)
    wrap = fields.Boolean(string="Sətrə keçir")
    wrap_lines = fields.Integer(string="Maks. sətir", default=2)

    static_text = fields.Char(string="Mətn")
    field_name = fields.Char(string="Sahə (texniki ad)")
    field_source = fields.Selection(
        [("product", "Məhsul"), ("doc", "Sənəd"), ("line", "Sətir")],
        string="Sahənin mənbəyi", default="product", required=True,
        help="Məhsul = məhsul kartı; Sənəd = çapın açıldığı sifariş/anbar sənədi; "
             "Sətir = həmin sənəddə bu məhsulun sətri.")
    short_name = fields.Boolean(
        string="Yalnız son hissə",
        help="«All / İçkilər / Su» kimi iyerarxik adlarda yalnız son hissəni yaz: «Su».")

    show_barcode_text = fields.Boolean(string="Nömrəni göstər", default=True)
    barcode_symbology = fields.Selection(
        [("code128", "Code 128"), ("ean13", "EAN-13")],
        string="Barkod növü", default="code128", required=True)
    module_width = fields.Integer(string="Modul eni (nöqtə)", default=2,
                                  help="Genişlik 0 olanda ən nazik zolağın eni.")
    price_amount_only = fields.Boolean(string="Yalnız məbləğ (valyutasız)")
    thickness_mm = fields.Float(string="Qalınlıq (mm)", default=0.3)

    # ------------------------------------------------------------------ #
    #  JSON <-> qeyd
    # ------------------------------------------------------------------ #
    def _to_dict(self):
        self.ensure_one()
        return {
            "id": self.id,
            "sequence": self.sequence,
            "type": self.element_type,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "font_pt": self.font_pt,
            "bold": self.bold,
            "align": self.align,
            "rotation": int(self.rotation or "0"),
            "wrap": self.wrap,
            "wrap_lines": self.wrap_lines,
            "text": self.static_text or "",
            "field_name": self.field_name or "",
            "field_source": self.field_source or "product",
            "short_name": self.short_name,
            "show_barcode_text": self.show_barcode_text,
            "barcode_symbology": self.barcode_symbology,
            "module_width": self.module_width,
            "price_amount_only": self.price_amount_only,
            "thickness_mm": self.thickness_mm,
        }

    @api.model
    def _dict_to_vals(self, d, sequence):
        def f(key, default=0.0):
            try:
                return float(d.get(key) if d.get(key) is not None else default)
            except (TypeError, ValueError):
                return default

        def i(key, default=0):
            try:
                return int(d.get(key) if d.get(key) is not None else default)
            except (TypeError, ValueError):
                return default

        etype = d.get("type") if d.get("type") in dict(ELEMENT_TYPES) else "text"
        align = d.get("align") if d.get("align") in dict(ALIGN) else "left"
        rot = str(i("rotation", 0) % 360)
        if rot not in dict(ROTATION):
            rot = "0"
        symb = d.get("barcode_symbology") if d.get("barcode_symbology") in ("code128", "ean13") else "code128"
        return {
            "sequence": sequence,
            "element_type": etype,
            "x_mm": f("x_mm"),
            "y_mm": f("y_mm"),
            "width_mm": f("width_mm"),
            "height_mm": f("height_mm", 10.0),
            "font_pt": max(3, i("font_pt", 9)),
            "bold": bool(d.get("bold")),
            "align": align,
            "rotation": rot,
            "wrap": bool(d.get("wrap")),
            "wrap_lines": max(1, i("wrap_lines", 2)),
            "static_text": d.get("text") or False,
            "field_name": d.get("field_name") or False,
            "field_source": d.get("field_source") if d.get("field_source") in ("product", "doc", "line") else "product",
            "short_name": bool(d.get("short_name")),
            "show_barcode_text": bool(d.get("show_barcode_text", True)),
            "barcode_symbology": symb,
            "module_width": max(1, i("module_width", 2)),
            "price_amount_only": bool(d.get("price_amount_only")),
            "thickness_mm": f("thickness_mm", 0.3),
        }
