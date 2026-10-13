# -*- coding: utf-8 -*-
import secrets

from odoo import fields, models

TOKEN_PARAM = "label_print_direct.agent_token"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    alp_agent_token = fields.Char(
        string="Agent tokeni", config_parameter=TOKEN_PARAM,
        help="Agent bu token ilə tanınır. Agentin config faylına köçürün; sızsa yenidən yaradın.")
    alp_server_url = fields.Char(string="Server ünvanı", compute="_compute_alp_server_url")
    alp_confirm_threshold = fields.Integer(
        string="Təsdiq həddi (etiket sayı)", default=20,
        config_parameter="label_print_direct.confirm_threshold",
        help="Bir çapda bu saydan çox etiket göndəriləndə istifadəçidən təsdiq istənir. "
             "0 = heç vaxt soruşma.")

    def _compute_alp_server_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for rec in self:
            rec.alp_server_url = base

    def action_alp_regenerate_token(self):
        self.env["ir.config_parameter"].sudo().set_param(TOKEN_PARAM, secrets.token_urlsafe(24))
        return {"type": "ir.actions.client", "tag": "reload"}
