# -*- coding: utf-8 -*-
"""Printer: agentin tanıdığı açar + printer mühərrikinin parametrləri + yüklənmiş etiket ölçüsü.

Format yalnız printeri seçir; DPI, sürət, qaralıq, dil və rulondakı etiket
ölçüsü buradadır. Ad «40 × 30 · Xprinter XP-420B» kimi görünür.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .label_catalog import PRINTER_LANGUAGES
from .label_render import fmt_num


class LabelPrinter(models.Model):
    _name = "label.printer"
    _description = "Etiket printeri"
    _order = "sequence, id"

    name = fields.Char(string="Ad", required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    printer_key = fields.Char(
        string="Printer açarı", required=True, default="default",
        help="Agent bu açara görə işi hansı fiziki printerə göndərəcəyini bilir. "
             "Agentin printer xəritəsində eyni açar yazılır.")

    # Model və mühərrik
    model_id = fields.Many2one("label.printer.model", string="Model",
                               help="Kataloqdan seçin — DPI, sürət, qaralıq və dil özü dolur.")
    language = fields.Selection(PRINTER_LANGUAGES, string="Dil", default="tspl", required=True,
                                help="TSPL = etiket printeri (bitmap); ESC/POS = çek printeri (raster).")
    dpi = fields.Integer(string="DPI", default=203, help="XPrinter XP-4xx adətən 203 DPI.")
    max_width_mm = fields.Float(string="Maks. çap eni (mm)", default=108.0)
    speed = fields.Integer(string="Sürət", default=3, help="TSPL sürəti (düym/san).")
    density = fields.Integer(string="Qaralıq", default=8, help="0–15, çox = daha qara.")
    direction = fields.Selection([("1", "Normal"), ("0", "Tərs")], string="İstiqamət",
                                 default="1", required=True,
                                 help="Etiket başıaşağı çıxırsa dəyişin.")

    # Yüklənmiş etiket (rulon)
    size_id = fields.Many2one("label.size", string="Etiket ölçüsü",
                              help="Kataloqdan seçin — en, uzunluq, aralıq özü dolur.")
    width_mm = fields.Float(string="En (mm)", default=40.0)
    height_mm = fields.Float(string="Uzunluq (mm)", default=30.0)
    gap_mm = fields.Float(string="Aralıq (mm)", default=2.0, help="Rulonda iki etiket arasındakı boşluq.")

    note = fields.Char(string="Qeyd", help="məs. Anbar, USB; Kassa 2, şəbəkə 192.168.1.50")
    format_ids = fields.One2many("label.format", "printer_id", string="Formatlar")
    format_count = fields.Integer(compute="_compute_format_count")

    _sql_constraints = [("printer_key_uniq", "unique(printer_key)", "Printer açarı təkrarlana bilməz.")]

    # ------------------------------------------------------------------ #
    @api.depends("name", "width_mm", "height_mm")
    def _compute_display_name(self):
        for rec in self:
            size = ""
            if rec.width_mm and rec.height_mm:
                size = "%s × %s" % (fmt_num(rec.width_mm), fmt_num(rec.height_mm))
            rec.display_name = ("%s · %s" % (size, rec.name)) if size else (rec.name or "")

    @api.onchange("model_id")
    def _onchange_model_id(self):
        m = self.model_id
        if not m:
            return
        self.dpi = m.dpi or 203
        self.speed = m.speed or 3
        self.density = m.density if m.density is not None else 8
        self.language = m.language or "tspl"
        self.max_width_mm = m.max_width_mm or 108.0
        old_model_name = self._origin.model_id.name if self._origin and self._origin.model_id else None
        if not self.name or self.name in ("Etiket printeri", old_model_name):
            self.name = m.name

    @api.onchange("size_id")
    def _onchange_size_id(self):
        s = self.size_id
        if s:
            self.width_mm, self.height_mm, self.gap_mm = s.width_mm, s.height_mm, s.gap_mm

    @api.constrains("dpi", "density", "speed", "width_mm", "height_mm")
    def _check_engine(self):
        for rec in self:
            if rec.dpi not in (203, 300, 600):
                raise ValidationError(_("DPI 203, 300 və ya 600 olmalıdır."))
            if not 0 <= rec.density <= 15:
                raise ValidationError(_("Qaralıq 0–15 arasında olmalıdır."))
            if not 1 <= rec.speed <= 12:
                raise ValidationError(_("Sürət 1–12 arasında olmalıdır."))
            if rec.width_mm <= 0 or rec.height_mm <= 0:
                raise ValidationError(_("Etiketin eni və uzunluğu müsbət olmalıdır."))
            if rec.max_width_mm and rec.width_mm > rec.max_width_mm + 0.01:
                raise ValidationError(_("Etiketin eni (%s mm) printerin maksimum çap enindən (%s mm) böyükdür.")
                                      % (fmt_num(rec.width_mm), fmt_num(rec.max_width_mm)))

    def _compute_format_count(self):
        counts = dict(self.env["label.format"]._read_group(
            [("printer_id", "in", self.ids)], ["printer_id"], ["__count"]))
        for rec in self:
            rec.format_count = counts.get(rec, 0)

    # ------------------------------------------------------------------ #
    def action_test_print(self):
        self.ensure_one()
        fmt = self.env["label.format"].search([("printer_id", "=", self.id)], limit=1)
        if not fmt:
            raise UserError(_("Bu printerə bağlı format yoxdur. Əvvəl bir format yaradın."))
        return fmt.action_test_print()

    def action_open_formats(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("label_print_direct.action_label_format")
        action["domain"] = [("printer_id", "=", self.id)]
        action["context"] = {"default_printer_id": self.id}
        return action
