# -*- coding: utf-8 -*-
"""Etiketin istifadə yeri: formatın hansı modullardan çap oluna biləcəyi.

Hər istifadə yerinin (istəyə görə) bir sənəd modeli və sətir modeli var —
dizaynerdə «Sahə» elementi məhsuldan əlavə bu modellərin sahələrini də
götürə bilir (məs. satış sifarişində müştəri adı, sifariş nömrəsi).
"""
from odoo import fields, models


class LabelUsage(models.Model):
    _name = "label.usage"
    _description = "Etiket istifadə yeri"
    _order = "sequence, id"

    name = fields.Char(string="Ad", required=True)
    code = fields.Char(string="Kod", required=True)
    sequence = fields.Integer(default=10)
    doc_model = fields.Char(string="Sənəd modeli", help="məs. sale.order")
    doc_label = fields.Char(string="Sənəd adı", help="Dizaynerdə qrup başlığı, məs. «Satış sifarişi»")
    line_model = fields.Char(string="Sətir modeli", help="məs. sale.order.line")
    line_label = fields.Char(string="Sətir adı", help="Dizaynerdə qrup başlığı, məs. «Satış sətri»")

    _sql_constraints = [("code_uniq", "unique(code)", "İstifadə yerinin kodu təkrarlana bilməz.")]
