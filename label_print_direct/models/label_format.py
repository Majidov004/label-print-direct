# -*- coding: utf-8 -*-
"""Etiket formatı: fiziki ölçü + printer parametrləri + dizayn (elementlər).

Format məhsulu götürüb printerin dilində hazır iş (TSPL bitmap) qaytarır.
Dizaynerin canlı önizləməsi də eyni render yolundan keçir.

Formatın «istifadə yerləri» (label.usage) çap pəncərəsində hansı formatların
təklif olunacağını və dizaynerdə hansı sənəd/sətir sahələrinin görünəcəyini
müəyyən edir.
"""
import json
import logging
from collections import defaultdict

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import label_render as R

_logger = logging.getLogger(__name__)

_ELEMENT_JSON_DEPENDS = [
    "element_ids", "element_ids.sequence", "element_ids.element_type",
    "element_ids.x_mm", "element_ids.y_mm", "element_ids.width_mm", "element_ids.height_mm",
    "element_ids.font_pt", "element_ids.bold", "element_ids.align", "element_ids.rotation",
    "element_ids.wrap", "element_ids.wrap_lines", "element_ids.static_text",
    "element_ids.field_name", "element_ids.field_source", "element_ids.short_name",
    "element_ids.show_barcode_text",
    "element_ids.barcode_symbology", "element_ids.module_width", "element_ids.price_amount_only",
    "element_ids.thickness_mm",
]

# Dizaynerin «Sahə» siyahısına düşən sahə tipləri və gizlədilən texniki sahələr.
FIELD_TYPES = {"char", "text", "float", "integer", "monetary", "selection",
               "many2one", "date", "datetime", "boolean"}
SKIP_PREFIX = ("message_", "activity_", "website_", "image_", "product_variant",
               "write_", "create_", "rating_", "my_activity", "has_", "is_", "avatar_",
               "variant_", "attribute_", "combination_", "access_", "show_", "can_",
               "invoice_status", "picking_policy", "signed_", "require_")
SKIP_NAMES = {"id", "display_name", "barcode", "default_code", "name", "list_price",
              "lst_price", "currency_id", "company_id", "__last_update"}


class LabelFormat(models.Model):
    _name = "label.format"
    _description = "Etiket formatı"
    _order = "sequence, id"

    name = fields.Char(string="Ad", required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    note = fields.Char(string="Qeyd")
    usage_ids = fields.Many2many(
        "label.usage", string="Harada istifadə olunur",
        default=lambda self: self._default_usages(),
        help="Bu format hansı yerlərdən çap ediləndə təklif olunsun. Dizaynerdəki "
             "«Sahə» elementi də bu yerlərin sənəd/sətir sahələrini göstərir.")
    usage_codes = fields.Char(compute="_compute_usage_codes")
    company_ids = fields.Many2many(
        "res.company", string="Şirkətlər",
        default=lambda self: self.env.company,
        help="Format hansı şirkətlərdə (filiallarda) görünsün. Boş = hamısında.")

    # Fiziki etiket
    width_mm = fields.Float(string="En (mm)", required=True, default=40.0)
    height_mm = fields.Float(string="Uzunluq (mm)", required=True, default=30.0)
    gap_mm = fields.Float(string="Aralıq (mm)", default=2.0,
                          help="Rulonda iki etiket arasındakı boşluq.")

    # Printer (Konfiqurator → Printerlər). DPI, sürət, qaralıq printerin qeydindədir.
    printer_id = fields.Many2one(
        "label.printer", string="Printer", required=True, ondelete="restrict",
        default=lambda self: self.env["label.printer"].search([], limit=1),
        help="Etiket hansı printerə gedəcək. Printerin parametrləri Konfiqurator → Printerlər-dədir.")
    printer_key = fields.Char(related="printer_id.printer_key", string="Printer açarı")
    dpi = fields.Integer(related="printer_id.dpi")
    speed = fields.Integer(related="printer_id.speed")
    density = fields.Integer(related="printer_id.density")
    direction = fields.Selection(related="printer_id.direction")
    price_tax_mode = fields.Selection(
        [("incl", "Vergi daxil (satış qiyməti)"), ("excl", "Vergisiz")],
        string="Qiymət", default="incl", required=True)

    # Dizayn
    element_ids = fields.One2many("label.element", "format_id", string="Elementlər", copy=True)
    layout_json = fields.Text(
        string="Dizayn (JSON)", compute="_compute_layout_json", inverse="_inverse_layout_json",
        help="Dizaynerin oxuyub-yazdığı element siyahısı.")
    job_count = fields.Integer(compute="_compute_job_count")

    @api.model
    def _default_usages(self):
        return self.env["label.usage"].search([("code", "in", ("product", "stock"))])

    @api.constrains("width_mm", "height_mm")
    def _check_size(self):
        for rec in self:
            if rec.width_mm <= 0 or rec.height_mm <= 0:
                raise ValidationError(_("Etiketin eni və uzunluğu müsbət olmalıdır."))

    @api.depends("usage_ids")
    def _compute_usage_codes(self):
        for rec in self:
            rec.usage_codes = ",".join(sorted(rec.usage_ids.mapped("code")))

    # ------------------------------------------------------------------ #
    #  Sənəd formlarındakı dinamik çap düymələri
    # ------------------------------------------------------------------ #
    MAX_FORM_BUTTONS = 3

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._clear_view_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {"name", "usage_ids", "company_ids", "active", "sequence"} & set(vals):
            self._clear_view_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self._clear_view_cache()
        return res

    @api.model
    def _clear_view_cache(self):
        """Satış/alış formunun başlığındakı düymələr görünüş keşində saxlanır."""
        self.env.registry.clear_cache()

    @api.model
    def _inject_print_buttons(self, arch, usages):
        """Formun <header>-inə hər format üçün öz adı ilə düymə qoyur (istifadə yeri
        başına 3-ə qədər). Çox olanda düymə qoyulmur — Action menyusundakı «Etiket
        çap et» qalır. ``usages`` = [(kod, düymənin ``invisible`` ifadəsi)] — məs.
        qiymət təklifi düymələri yalnız təsdiqlənməmiş sifarişdə görünür."""
        header = arch.find(".//header")
        if header is None:
            return
        for code, invisible in usages:
            formats = self.search([
                ("usage_ids.code", "=", code),
                "|", ("company_ids", "=", False), ("company_ids", "in", self.env.companies.ids),
            ], order="sequence, id")
            if not formats or len(formats) > self.MAX_FORM_BUTTONS:
                continue
            for fmt in formats:
                attrs = {
                    "name": "action_print_labels_direct", "type": "object",
                    "string": fmt.name, "icon": "fa-tag",
                    "context": "{'label_format_id': %d, 'label_usage': '%s'}" % (fmt.id, code),
                }
                if invisible:
                    attrs["invisible"] = invisible
                etree.SubElement(header, "button", attrs)

    # ------------------------------------------------------------------ #
    #  JSON körpüsü (dizayner)
    # ------------------------------------------------------------------ #
    @api.depends(*_ELEMENT_JSON_DEPENDS)
    def _compute_layout_json(self):
        for rec in self:
            rec.layout_json = json.dumps(
                [el._to_dict() for el in rec.element_ids.sorted("sequence")],
                ensure_ascii=False)

    def _inverse_layout_json(self):
        Element = self.env["label.element"]
        for rec in self:
            try:
                items = json.loads(rec.layout_json or "[]")
            except (TypeError, ValueError):
                raise UserError(_("Dizayn məlumatı oxunmadı (JSON xətası)."))
            if not isinstance(items, list):
                items = []
            rec.element_ids.unlink()
            vals = []
            for n, d in enumerate(items):
                if isinstance(d, dict):
                    v = Element._dict_to_vals(d, (n + 1) * 10)
                    v["format_id"] = rec.id
                    vals.append(v)
            if vals:
                Element.create(vals)

    def _compute_job_count(self):
        counts = dict(self.env["label.print.job"]._read_group(
            [("format_id", "in", self.ids)], ["format_id"], ["__count"]))
        for rec in self:
            rec.job_count = counts.get(rec, 0)

    # ------------------------------------------------------------------ #
    #  Dizayner üçün sahə siyahısı
    # ------------------------------------------------------------------ #
    @api.model
    def get_field_options(self, usage_codes=None):
        """«Sahə» elementi üçün mənbə qruplarına görə sahələr:
        [{source, group, name, label}]. Məhsul həmişə var; sənəd/sətir
        qrupları formatın istifadə yerlərinə görə gəlir."""
        out = self._model_field_options("product.product", "product", "Məhsul")
        seen = set()
        usages = self.env["label.usage"].search(
            [("code", "in", list(usage_codes or []))], order="sequence, id")
        for u in usages:
            for model_name, source, label in ((u.doc_model, "doc", u.doc_label),
                                              (u.line_model, "line", u.line_label)):
                if model_name and model_name in self.env and (source, model_name) not in seen:
                    seen.add((source, model_name))
                    out += self._model_field_options(model_name, source, label or model_name)
        return out

    @api.model
    def _model_field_options(self, model_name, source, group):
        fg = self.env[model_name].fields_get(attributes=["string", "type"])
        items = []
        for name, f in fg.items():
            if f.get("type") not in FIELD_TYPES or name.startswith(SKIP_PREFIX) or name in SKIP_NAMES:
                continue
            items.append({"source": source, "group": group, "name": name,
                          "label": f.get("string") or name})
        items.sort(key=lambda d: d["label"].lower())
        return items

    # ------------------------------------------------------------------ #
    #  Render
    # ------------------------------------------------------------------ #
    def _spec(self, elements=None):
        self.ensure_one()
        return {
            "width_mm": self.width_mm, "height_mm": self.height_mm, "gap_mm": self.gap_mm,
            "dpi": self.dpi, "speed": self.speed, "density": self.density,
            "direction": self.direction,
            "elements": elements if elements is not None
            else [el._to_dict() for el in self.element_ids.sorted("sequence")],
        }

    @api.model
    def preview_from_spec(self, spec):
        """Dizayner: saxlanmamış dizayn üçün canlı PNG (nümunə məlumatla)."""
        spec = dict(spec or {})
        try:
            spec["width_mm"] = max(5.0, min(300.0, float(spec.get("width_mm") or 40)))
            spec["height_mm"] = max(5.0, min(300.0, float(spec.get("height_mm") or 30)))
            spec["dpi"] = int(spec.get("dpi") or 203)
        except (TypeError, ValueError):
            spec.update(width_mm=40.0, height_mm=30.0, dpi=203)
        img = R.render_label(spec, self._sample_data(spec))
        return R.png_data_url(img, scale=2)

    @api.model
    def _sample_data(self, spec):
        """Önizləmə/test üçün nümunə məlumat: sənəd/sətir sahələri {sahə} kimi görünür."""
        data = dict(R.SAMPLE_DATA)
        data["fields"] = {}
        for el in spec.get("elements") or []:
            if not isinstance(el, dict):
                continue
            if el.get("type") == "field" and el.get("field_name"):
                data["fields"][R.field_key(el)] = "{%s}" % el["field_name"]
            elif el.get("type") == "text":
                for src, fname in R.text_placeholders(el.get("text") or ""):
                    data["fields"]["%s:%s" % (src, fname)] = "{%s}" % fname
        return data

    def _format_field_value(self, record, fname):
        if record is None:
            return ""
        field = record._fields.get(fname)
        if field is None:
            return ""
        try:
            val = record[fname]
        except Exception:
            return ""
        if val is False or val is None:
            return ""
        if field.type in ("float", "monetary"):
            return "%.2f" % val
        if field.type == "boolean":
            return "Bəli"
        try:
            return str(field.convert_to_export(val, record) or "")
        except Exception:
            return str(val)

    def _price_parts(self, product, pricelist=None):
        """(məbləğ mətni, valyuta simvolu). Formatın vergi rejiminə görə."""
        self.ensure_one()
        is_variant = product._name == "product.product"
        variant = product if is_variant else product.product_variant_id
        currency = product.currency_id
        price = product.lst_price if is_variant else product.list_price
        if pricelist and variant:
            try:
                price = pricelist._get_product_price(variant, 1.0)
                currency = pricelist.currency_id or currency
            except Exception:
                pass
        taxes = product.taxes_id
        if taxes and variant:
            try:
                res = taxes.compute_all(price, currency=currency, quantity=1.0, product=variant)
                price = res["total_included"] if self.price_tax_mode == "incl" else res["total_excluded"]
            except Exception:
                pass
        return "%.2f" % (price or 0.0), (currency.symbol or "")

    def _product_data(self, product, pricelist=None, doc=None, line=None):
        """Render üçün məlumat: məhsul + (varsa) çapın açıldığı sənəd və sətir."""
        self.ensure_one()
        amount, symbol = self._price_parts(product, pricelist)
        is_variant = product._name == "product.product"
        variant = product if is_variant else (product.product_variant_id or product)
        data = {
            "name": product.display_name or "",
            "name_plain": product.name or "",
            "code": product.default_code or "",
            "barcode": (product.barcode or "") if "barcode" in product._fields else "",
            "price_amount": amount,
            "currency": symbol,
            "fields": {},
        }
        sources = {"product": variant, "doc": doc or None, "line": line or None}
        for el in self.element_ids:
            if el.element_type == "field" and el.field_name:
                src = el.field_source or "product"
                key = "%s:%s" % (src, el.field_name)
                if key not in data["fields"]:       # «yalnız son hissə» render zamanı element üzrə tətbiq olunur
                    data["fields"][key] = self._format_field_value(sources.get(src), el.field_name)
            elif el.element_type == "text" and el.static_text:
                for src, fname in R.text_placeholders(el.static_text):    # {doc.name} kimi
                    key = "%s:%s" % (src, fname)
                    if key not in data["fields"]:
                        data["fields"][key] = self._format_field_value(sources.get(src), fname)
        return data

    def _payload_for(self, img, copies=1):
        """Şəkli printerin dilinə bükür: TSPL bitmap (etiket) və ya ESC/POS raster (çek)."""
        self.ensure_one()
        if self.printer_id.language == "escpos":
            return R.payload_b64(R.escpos_raster_payload(img, copies))
        return R.payload_b64(R.tspl_bitmap_payload(self._spec(), img, copies))

    def render_job_payload(self, product, copies=1, pricelist=None, doc=None, line=None):
        """Bir məhsul üçün hazır iş: '#B64#…' (printerin dilində, base64)."""
        self.ensure_one()
        img = R.render_label(self._spec(), self._product_data(product, pricelist, doc=doc, line=line))
        return self._payload_for(img, copies)

    def _test_payload(self):
        self.ensure_one()
        spec = self._spec()
        img = R.render_label(spec, self._sample_data(spec))
        return self._payload_for(img, 1)

    @api.onchange("printer_id")
    def _onchange_printer_id(self):
        """Yeni format: ölçü printerə yüklənmiş rulondan gəlir, ad da ondan təklif olunur."""
        p = self.printer_id
        if p and p.width_mm and p.height_mm:
            self.width_mm, self.height_mm, self.gap_mm = p.width_mm, p.height_mm, p.gap_mm
            if not self.name:
                self.name = p.display_name

    # ------------------------------------------------------------------ #
    #  Sənəddən çap (satış / alış sifarişi)
    # ------------------------------------------------------------------ #
    @api.model
    def _open_label_wizard(self, docs, lines, qty_field, format_id=None, usage=None):
        """Sənəd(lər)in sətirlərini standart «Print Labels» pəncərəsinə ötürür:
        məhsullar + sənəddəki miqdar + (tək sənəddə) sənədin özü dizayner sahələri üçün."""
        qty = defaultdict(float)
        for line in lines:
            if not line.product_id or getattr(line, "display_type", False):
                continue
            qty[line.product_id.id] += line[qty_field] or 0.0
        if not qty:
            raise UserError(_("Sənəddə çap ediləsi məhsul sətri yoxdur."))
        ctx = {
            "default_product_ids": [(6, 0, list(qty))],
            "default_print_target": "label",
            "label_doc_model": docs._name,
            "label_qty_map": json.dumps({str(p): max(1, int(round(q))) for p, q in qty.items()}),
        }
        if len(docs) == 1:
            ctx["label_doc_id"] = docs.id
        if format_id:
            ctx["label_format_id"] = int(format_id)
        if usage:
            ctx["label_usage"] = usage
        return {
            "name": _("Etiket çapı"),
            "type": "ir.actions.act_window",
            "res_model": "product.label.layout",
            "views": [(self.env.ref("product.product_label_layout_form").id, "form")],
            "target": "new",
            "context": ctx,
        }

    # ------------------------------------------------------------------ #
    #  Düymələr
    # ------------------------------------------------------------------ #
    def action_test_print(self):
        self.ensure_one()
        job = self.env["label.print.job"].create({
            "format_id": self.id,
            "printer_key": self.printer_key or "default",
            "company_id": self.env.company.id,
            "product_name": _("Test etiketi"),
            "copies": 1,
            "payload": self._test_payload(),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Test etiketi növbəyə qoyuldu"),
                "message": _("İş %s. Agent işləyirsə bir neçə saniyəyə çıxacaq.") % job.name,
                "type": "success",
                "sticky": False,
            },
        }

    def action_generate_default_layout(self):
        """Standart dizayn: ad (yuxarı), barkod (orta), qiymət (aşağı sağ)."""
        Element = self.env["label.element"]
        for fmt in self:
            fmt.element_ids.unlink()
            Element.create([dict(v, format_id=fmt.id)
                            for v in self._default_layout_vals(fmt.width_mm, fmt.height_mm)])
        return True

    @staticmethod
    def _default_layout_vals(w, h):
        """Ölçüyə uyğun standart dizayn (kiçik etiketdə 1 sətir ad, xırda şrift)."""
        pt_mm = 25.4 / 72.0
        margin = 2.0
        inner = max(10.0, w - 2 * margin)
        tall = h >= 26
        name_pt, name_lines = (9, 2) if tall else (7, 1)
        price_pt = 11 if tall else 9
        bc_text_pt = 7 if tall else 6
        top = 1.0
        name_h = name_pt * pt_mm * 1.2 * name_lines + 0.5
        barcode_y = round(top + name_h, 1)
        price_h = price_pt * pt_mm * 1.25
        price_y = round(h - price_h - 1.0, 1)
        bc_text_h = bc_text_pt * pt_mm * 1.3
        bar_h = round(max(4.0, price_y - 0.8 - bc_text_h - barcode_y), 1)
        return [
            {"sequence": 10, "element_type": "name", "x_mm": margin, "y_mm": top,
             "width_mm": inner, "font_pt": name_pt, "bold": True,
             "wrap": True, "wrap_lines": name_lines},
            {"sequence": 20, "element_type": "barcode", "x_mm": margin, "y_mm": barcode_y,
             "width_mm": inner, "height_mm": bar_h, "font_pt": bc_text_pt,
             "show_barcode_text": True},
            {"sequence": 30, "element_type": "price", "x_mm": margin, "y_mm": price_y,
             "width_mm": inner, "font_pt": price_pt, "bold": True, "align": "right"},
        ]

    def action_open_jobs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("label_print_direct.action_label_print_job")
        action["domain"] = [("format_id", "=", self.id)]
        action["context"] = {"default_format_id": self.id}
        return action
