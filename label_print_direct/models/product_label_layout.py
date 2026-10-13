# -*- coding: utf-8 -*-
"""Standart «Print Labels» pəncərəsinə «Etiket printeri» yolu.

Miqdarlar Odoo-nun öz məntiqi ilə gəlir (`_prepare_report_data`): məhsul
siyahısından çapda «Quantity», anbar qəbulundan çapda əməliyyat miqdarları,
satış/alış sifarişindən çapda sifariş miqdarı (label_qty_map).

Pəncərə haradan açılıbsa (məhsul / anbar / satış / alış) yalnız o istifadə
yerinə aid formatlar təklif olunur; çapın açıldığı sənəd və sətir dizaynerdəki
«Sənəd / Sətir» sahələri üçün render-ə ötürülür.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CONFIRM_PARAM = "label_print_direct.confirm_threshold"
DOC_USAGE = {"sale.order": "sale", "purchase.order": "purchase", "stock.picking": "stock"}


class ProductLabelLayout(models.TransientModel):
    _inherit = "product.label.layout"

    print_target = fields.Selection(
        [("label", "Etiket printeri (birbaşa)"), ("sheet", "A4 vərəq (PDF)")],
        string="Çap üsulu", required=True, default="sheet")
    label_format_id = fields.Many2one(
        "label.format", string="Etiket formatı",
        domain="[('usage_ids.code', '=', label_usage), "
               "'|', ('company_ids', '=', False), ('company_ids', 'in', allowed_company_ids)]")
    label_usage = fields.Char(compute="_compute_label_usage")
    label_usage_ctx = fields.Char(default=lambda self: self.env.context.get("label_usage") or False)
    label_doc_model = fields.Char(default=lambda self: self.env.context.get("label_doc_model") or False)
    label_doc_id = fields.Integer(default=lambda self: int(self.env.context.get("label_doc_id") or 0))
    label_qty_mode = fields.Selection(
        [("doc", "Sənəddəki miqdar"), ("custom", "Quantity sahəsi")],
        string="Miqdar", default="doc")

    # Böyük çapdan qoruma: cəmi etiket sayı həddi keçəndə təsdiq istənir.
    # Göndərilmiş iş printerin yaddaşından ləğv edilə bilmir, ona görə əvvəlcədən soruşuruq.
    label_total_qty = fields.Integer(string="Cəmi etiket", compute="_compute_label_total")
    label_confirm_threshold = fields.Integer(compute="_compute_label_total")
    label_needs_confirm = fields.Boolean(compute="_compute_label_total")
    label_confirmed = fields.Boolean(string="Bəli, bu qədər etiketi çap et")

    # ------------------------------------------------------------------ #
    #  İstifadə yeri və standart format
    # ------------------------------------------------------------------ #
    @api.model
    def _usage_from_context(self):
        ctx = self.env.context
        if ctx.get("label_usage"):
            return ctx["label_usage"]
        if ctx.get("default_move_ids"):
            return "stock"
        return DOC_USAGE.get(ctx.get("label_doc_model"), "product")

    @api.depends("move_ids", "label_doc_model", "label_usage_ctx")
    def _compute_label_usage(self):
        for wiz in self:
            if wiz.label_usage_ctx:
                wiz.label_usage = wiz.label_usage_ctx
            elif wiz.move_ids:
                wiz.label_usage = "stock"
            else:
                wiz.label_usage = DOC_USAGE.get(wiz.label_doc_model, "product")

    def _label_source_label(self):
        usage = self.env["label.usage"].search([("code", "=", self.label_usage)], limit=1)
        return usage.name or self.label_usage or ""

    @api.model
    def _find_format(self, usage):
        Fmt = self.env["label.format"]
        base = [("usage_ids.code", "=", usage)]
        return (Fmt.search(base + [("company_ids", "in", [self.env.company.id])], limit=1)
                or Fmt.search(base + [("company_ids", "=", False)], limit=1))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "label_format_id" in fields_list and not res.get("label_format_id"):
            fmt = None
            if self.env.context.get("label_format_id"):      # formdakı format düyməsi
                fmt = self.env["label.format"].browse(int(self.env.context["label_format_id"])).exists()
            fmt = fmt or self._find_format(self._usage_from_context())
            if fmt:
                res["label_format_id"] = fmt.id
                if "print_target" in fields_list and "default_print_target" not in self.env.context:
                    res["print_target"] = "label"
        return res

    # ------------------------------------------------------------------ #
    #  Miqdar
    # ------------------------------------------------------------------ #
    @api.model
    def _label_confirm_threshold(self):
        try:
            return int(self.env["ir.config_parameter"].sudo().get_param(CONFIRM_PARAM, "20") or 0)
        except (TypeError, ValueError):
            return 20

    def _prepare_report_data(self):
        xml_id, data = super()._prepare_report_data()
        qmap = self.env.context.get("label_qty_map")
        if (qmap and self.print_target == "label" and self.label_qty_mode == "doc"
                and data.get("active_model") == "product.product"):
            try:
                qmap = json.loads(qmap) if isinstance(qmap, str) else dict(qmap)
            except (TypeError, ValueError):
                qmap = {}
            if qmap:
                data["quantity_by_product"] = {
                    pid: int(qmap.get(str(pid), qmap.get(pid, q)) or 0)
                    for pid, q in data["quantity_by_product"].items()}
        return xml_id, data

    @api.depends("custom_quantity", "product_ids", "product_tmpl_ids",
                 "move_quantity", "move_ids", "print_target", "label_qty_mode")
    def _compute_label_total(self):
        threshold = self._label_confirm_threshold()
        for wiz in self:
            total = 0
            if wiz.print_target == "label":
                try:
                    _xml_id, data = wiz._prepare_report_data()
                    total = sum(int(q or 0) for q in (data.get("quantity_by_product") or {}).values())
                except Exception:
                    n = len(wiz.product_ids) or len(wiz.product_tmpl_ids)
                    total = n * max(0, wiz.custom_quantity or 0)
            wiz.label_total_qty = total
            wiz.label_confirm_threshold = threshold
            wiz.label_needs_confirm = threshold > 0 and total > threshold

    # ------------------------------------------------------------------ #
    #  Çap
    # ------------------------------------------------------------------ #
    def process(self):
        self.ensure_one()
        if self.print_target == "label":
            return self._process_label_direct()
        return super().process()

    def _label_doc(self):
        """Çapın açıldığı sənəd (sifariş / anbar sənədi), yoxdursa None."""
        if self.label_doc_model and self.label_doc_id and self.label_doc_model in self.env:
            return self.env[self.label_doc_model].browse(self.label_doc_id).exists() or None
        if self.move_ids:
            return self.move_ids.picking_id[:1] or None
        return None

    def _label_line_for(self, product, doc):
        """Sənəddə bu məhsulun sətri (ilk uyğun gələn), yoxdursa None."""
        if doc is None:
            return None
        vid = product.id if product._name == "product.product" else product.product_variant_id.id
        if doc._name == "stock.picking":
            lines = (self.move_ids or doc.move_ids).filtered(lambda m: m.product_id.id == vid)
        elif "order_line" in doc._fields:
            lines = doc.order_line.filtered(lambda l: l.product_id.id == vid)
        else:
            return None
        return lines[:1] or None

    def _process_label_direct(self):
        fmt = self.label_format_id
        if not fmt:
            raise UserError(_("Etiket formatı seçin."))
        if self.label_needs_confirm and not self.label_confirmed:
            raise UserError(_(
                "Bu çap %(n)s etiket göndərəcək (hədd %(t)s). Göndərilən iş printerdən ləğv "
                "edilə bilmir. Davam etmək üçün təsdiq qutusunu işarələyin."
            ) % {"n": self.label_total_qty, "t": self.label_confirm_threshold})
        _xml_id, data = self._prepare_report_data()
        model = data.get("active_model")
        qty_by_product = data.get("quantity_by_product") or {}
        if not model or not qty_by_product:
            raise UserError(_("Çap üçün məhsul tapılmadı."))

        doc = self._label_doc()
        source_label = self._label_source_label()
        Job = self.env["label.print.job"]
        created = 0
        for pid, qty in qty_by_product.items():
            qty = int(qty or 0)
            if qty <= 0:
                continue
            product = self.env[model].browse(int(pid)).exists()
            if not product:
                continue
            line = self._label_line_for(product, doc)
            Job.create({
                "format_id": fmt.id,
                "printer_key": fmt.printer_key or "default",
                "company_id": self.env.company.id,
                "product_name": product.display_name,
                "copies": qty,
                "source_label": source_label,
                "doc_model": doc._name if doc is not None else False,
                "doc_id": doc.id if doc is not None else 0,
                "doc_ref": doc.display_name if doc is not None else False,
                "payload": fmt.render_job_payload(product, copies=qty, pricelist=self.pricelist_id,
                                                  doc=doc, line=line),
            })
            created += 1
        if not created:
            raise UserError(_("Miqdar sıfırdır, çap ediləcək etiket yoxdur."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Etiketlər növbəyə qoyuldu"),
                "message": _("%s iş çap növbəsinə göndərildi (%s).") % (created, fmt.name),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
