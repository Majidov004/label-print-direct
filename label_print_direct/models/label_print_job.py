# -*- coding: utf-8 -*-
"""Çap növbəsi.

Hər çap bir işdir. Agent «gözləyir» işləri çəkir (status «göndərildi» olur),
printerə yazır, sonra nəticəni bildirir («çap olundu» / «xəta»).
"""
from odoo import _, api, fields, models


class LabelPrintJob(models.Model):
    _name = "label.print.job"
    _description = "Etiket çap işi"
    _order = "id desc"

    name = fields.Char(default=lambda self: _("Yeni"), copy=False, readonly=True)
    format_id = fields.Many2one("label.format", string="Format", ondelete="set null")
    printer_key = fields.Char(string="Printer açarı", default="default", index=True, required=True)
    product_name = fields.Char(string="Məhsul")
    copies = fields.Integer(string="Nüsxə", default=1)
    source_label = fields.Char(string="Mənbə", help="Çapın açıldığı yer: Məhsullar, Satış sifarişi, Anbar…")
    doc_model = fields.Char(string="Sənəd modeli")
    doc_id = fields.Integer(string="Sənəd ID")
    doc_ref = fields.Char(string="Sənəd", help="Çapın göndərildiyi sənədin nömrəsi (varsa).")
    payload = fields.Text(string="Printer əmrləri",
                          help="Printerə olduğu kimi yazılan baytlar (#B64# = base64).")
    state = fields.Selection(
        [("pending", "Gözləyir"), ("sent", "Agentə göndərildi"),
         ("done", "Çap olundu"), ("error", "Xəta")],
        string="Status", default="pending", index=True, required=True)
    sent_date = fields.Datetime(string="Göndərildi", readonly=True)
    done_date = fields.Datetime(string="Bitdi", readonly=True)
    error_message = fields.Text(string="Xəta", readonly=True)
    company_id = fields.Many2one("res.company", string="Şirkət",
                                 default=lambda self: self.env.company, index=True)
    user_id = fields.Many2one("res.users", string="Kim",
                              default=lambda self: self.env.user, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("Yeni"):
                vals["name"] = self.env["ir.sequence"].next_by_code("label.print.job") or _("Yeni")
        return super().create(vals_list)

    def action_requeue(self):
        self.write({"state": "pending", "error_message": False,
                    "sent_date": False, "done_date": False})
        return True

    def action_mark_done(self):
        self.write({"state": "done", "done_date": fields.Datetime.now()})
        return True

    def action_open_doc(self):
        self.ensure_one()
        if not self.doc_model or not self.doc_id or self.doc_model not in self.env:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.doc_model,
            "res_id": self.doc_id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------ #
    #  Agent tərəfi (superuser ilə çağırılır)
    # ------------------------------------------------------------------ #
    @api.model
    def _agent_pull(self, printer_keys, limit=20):
        jobs = self.search([("state", "=", "pending"), ("printer_key", "in", printer_keys)],
                           order="id asc", limit=limit)
        result = [{
            "id": j.id, "name": j.name, "payload": j.payload or "",
            "copies": j.copies or 1, "printer_key": j.printer_key,
        } for j in jobs]
        if jobs:
            jobs.write({"state": "sent", "sent_date": fields.Datetime.now()})
        return result

    @api.model
    def _agent_ack(self, results):
        now = fields.Datetime.now()
        updated = 0
        for item in results or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self.browse(int(item.get("id"))).exists()
            except (TypeError, ValueError):
                continue
            if not job:
                continue
            if item.get("success"):
                job.write({"state": "done", "done_date": now, "error_message": False})
            else:
                job.write({"state": "error",
                           "error_message": str(item.get("error") or "Naməlum xəta")[:2000]})
            updated += 1
        return updated
