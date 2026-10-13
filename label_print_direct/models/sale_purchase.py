# -*- coding: utf-8 -*-
"""Sənəd və məhsul formalarında etiket çap düymələri.

* Formun başlığına formatların sayı qədər düymə qoyulur (hər düymə formatın
  adını daşıyır), istifadə yeri başına 3-dən çox olanda düymə qoyulmur —
  Action menyusundakı «Etiket çap et» ilə format seçilir.
* Sifarişlərdə vəziyyətə görə istifadə yeri fərqlənir: təsdiqlənməmiş satış
  sifarişi = «Qiymət təklifi», təsdiqlənməmiş alış = «Alış sorğusu (RFQ)».
* Sifarişlərdən sətirlərdəki məhsullar sifariş miqdarı ilə, məhsul kartından
  məhsulun özü, anbar sənədindən əməliyyat miqdarları ilə standart
  «Print Labels» pəncərəsinə düşür.
"""
from odoo import api, models


class LabelPrintButtonsMixin(models.AbstractModel):
    _name = "label.print.buttons.mixin"
    _description = "Etiket çap düymələri (formun başlığına dinamik)"
    # [(istifadə kodu, düymənin invisible ifadəsi və ya None)]
    _label_usages = []
    _label_qty_field = None

    def _label_usage_for_record(self):
        """Bu qeyd üçün hazırkı istifadə yeri (vəziyyətə görə)."""
        return self._label_usages[0][0] if self._label_usages else None

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "form" and self._label_usages:
            self.env["label.format"]._inject_print_buttons(arch, self._label_usages)
        return arch, view

    def action_print_labels_direct(self):
        ctx = self.env.context
        usage = ctx.get("label_usage") or (self[:1]._label_usage_for_record() if self else None)
        return self._label_wizard_action(ctx.get("label_format_id"), usage)

    def _label_wizard_action(self, format_id, usage):
        """Standart: sənəd sətirləri (sifarişlər). Məhsul/anbar öz variantını verir."""
        return self.env["label.format"]._open_label_wizard(
            self, self.mapped("order_line"), self._label_qty_field, format_id=format_id, usage=usage)

    def _label_extend_standard_action(self, action, format_id, usage):
        """Odoo-nun öz «Print Labels» action-ını bizim seçimlərlə tamamlayır."""
        ctx = dict(action.get("context") or {})
        ctx.update({"default_print_target": "label", "label_usage": usage})
        if format_id:
            ctx["label_format_id"] = int(format_id)
        action["context"] = ctx
        return action


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "label.print.buttons.mixin"]
    _label_usages = [
        ("quotation", "state not in ('draft', 'sent')"),
        ("sale", "state in ('draft', 'sent', 'cancel')"),
    ]
    _label_qty_field = "product_uom_qty"

    def _label_usage_for_record(self):
        self.ensure_one()
        return "quotation" if self.state in ("draft", "sent") else "sale"


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "label.print.buttons.mixin"]
    _label_usages = [
        ("rfq", "state not in ('draft', 'sent', 'to approve')"),
        ("purchase", "state in ('draft', 'sent', 'to approve', 'cancel')"),
    ]
    _label_qty_field = "product_qty"

    def _label_usage_for_record(self):
        self.ensure_one()
        return "rfq" if self.state in ("draft", "sent", "to approve") else "purchase"


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "label.print.buttons.mixin"]
    _label_usages = [("product", None)]

    def _label_wizard_action(self, format_id, usage):
        return self._label_extend_standard_action(self.action_open_label_layout(), format_id, "product")


class ProductProduct(models.Model):
    _name = "product.product"
    _inherit = ["product.product", "label.print.buttons.mixin"]
    _label_usages = [("product", None)]

    def _label_wizard_action(self, format_id, usage):
        return self._label_extend_standard_action(self.action_open_label_layout(), format_id, "product")


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "label.print.buttons.mixin"]
    _label_usages = [("stock", None)]

    def _label_wizard_action(self, format_id, usage):
        return self._label_extend_standard_action(self.action_open_label_layout(), format_id, "stock")
