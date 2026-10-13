# -*- coding: utf-8 -*-
import secrets

TOKEN_PARAM = "label_print_direct.agent_token"


def post_init_hook(env):
    """Quraşdırmada: agent tokeni + ilk printer + nümunə format (40 × 30 mm)."""
    icp = env["ir.config_parameter"].sudo()
    if not icp.get_param(TOKEN_PARAM):
        icp.set_param(TOKEN_PARAM, secrets.token_urlsafe(24))

    Printer = env["label.printer"].sudo()
    printer = Printer.search([], limit=1)
    if not printer:
        printer = Printer.create({"name": "Etiket printeri", "printer_key": "default"})

    Format = env["label.format"].sudo()
    if not Format.search([], limit=1):
        fmt = Format.create({
            "name": "40 × 30 mm",
            "width_mm": 40.0,
            "height_mm": 30.0,
            "gap_mm": 2.0,
            "printer_id": printer.id,
        })
        fmt.action_generate_default_layout()
