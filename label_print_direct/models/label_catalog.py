# -*- coding: utf-8 -*-
"""Kataloqlar: printer modelləri və məşhur etiket ölçüləri.

Printer qeydi yaradanda model seçilir → DPI, sürət, qaralıq, dil özü dolur;
ölçü seçilir → en, uzunluq, aralıq özü dolur.
"""
from odoo import api, fields, models

from .label_render import fmt_num

PRINTER_LANGUAGES = [("tspl", "TSPL (etiket printeri)"), ("escpos", "ESC/POS (çek printeri)")]


class LabelPrinterModel(models.Model):
    _name = "label.printer.model"
    _description = "Printer modeli"
    _order = "sequence, name"

    name = fields.Char(string="Model", required=True)
    brand = fields.Char(string="Marka")
    sequence = fields.Integer(default=10)
    language = fields.Selection(PRINTER_LANGUAGES, string="Dil", default="tspl", required=True)
    dpi = fields.Integer(string="DPI", default=203)
    max_width_mm = fields.Float(string="Maks. çap eni (mm)", default=108.0)
    speed = fields.Integer(string="Sürət", default=3)
    density = fields.Integer(string="Qaralıq", default=8)
    note = fields.Char(string="Qeyd")


class LabelSize(models.Model):
    _name = "label.size"
    _description = "Etiket ölçüsü"
    _order = "width_mm, height_mm"

    name = fields.Char(string="Ad", compute="_compute_name", store=True)
    width_mm = fields.Float(string="En (mm)", required=True)
    height_mm = fields.Float(string="Uzunluq (mm)", required=True)
    gap_mm = fields.Float(string="Aralıq (mm)", default=2.0)
    note = fields.Char(string="Qeyd")

    @api.depends("width_mm", "height_mm")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s × %s mm" % (fmt_num(rec.width_mm), fmt_num(rec.height_mm))
