# -*- coding: utf-8 -*-
"""Agentin istifadə etdiyi HTTP API (sadə JSON, JSON-RPC deyil).

POST /label_tspl/ping  {token}                                  -> {ok, server}
POST /label_tspl/test  {token, printer_key}                     -> {payload, format}
POST /label_tspl/pull  {token, printer_keys|printer_key, limit} -> {jobs: [...]}
POST /label_tspl/ack   {token, results: [{id, success, error}]} -> {ok, updated}

Baza: `X-Odoo-Database` başlığı, ya body-dəki `database`, ya sessiya.
Bütün sorğular agentdən gəlir; server agentə heç vaxt müraciət etmir.
"""
import json
import logging

from odoo import SUPERUSER_ID, api, http
from odoo.http import request
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

TOKEN_PARAM = "label_print_direct.agent_token"


class LabelAgentController(http.Controller):

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data, ensure_ascii=False),
            headers=[("Content-Type", "application/json; charset=utf-8")],
            status=status)

    def _payload(self):
        try:
            return json.loads(request.httprequest.get_data(as_text=True) or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def _run(self, handler):
        data = self._payload()
        db = (request.httprequest.headers.get("X-Odoo-Database")
              or data.get("database") or request.db)
        if not db:
            return self._json({"error": "no_database",
                               "detail": "X-Odoo-Database başlığı və ya 'database' sahəsi lazımdır."}, 400)
        try:
            registry = Registry(db)
        except Exception:
            return self._json({"error": "no_database", "detail": "Baza tapılmadı: %s" % db}, 404)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            expected = env["ir.config_parameter"].get_param(TOKEN_PARAM)
            token = data.get("token")
            if not expected or not token or token != expected:
                return self._json({"error": "invalid_token"}, 403)
            try:
                return self._json(handler(env, data))
            except Exception as e:
                _logger.exception("Agent API xətası")
                return self._json({"error": "server_error", "detail": str(e)[:300]}, 500)

    @http.route("/label_tspl/ping", type="http", auth="none", methods=["POST"], csrf=False)
    def ping(self, **kw):
        def handler(env, data):
            return {"ok": True, "server": env["ir.config_parameter"].get_param("web.base.url")}
        return self._run(handler)

    @http.route("/label_tspl/test", type="http", auth="none", methods=["POST"], csrf=False)
    def test(self, **kw):
        def handler(env, data):
            key = data.get("printer_key") or "default"
            Fmt = env["label.format"]
            fmt = Fmt.search([("printer_id.printer_key", "=", key)], order="sequence, id", limit=1) \
                or Fmt.search([], order="sequence, id", limit=1)
            if not fmt:
                return {"error": "no_format"}
            return {"payload": fmt._test_payload(), "format": fmt.name}
        return self._run(handler)

    @http.route("/label_tspl/pull", type="http", auth="none", methods=["POST"], csrf=False)
    def pull(self, **kw):
        def handler(env, data):
            try:
                limit = max(1, min(100, int(data.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            keys = data.get("printer_keys")
            if not (isinstance(keys, list) and keys):
                keys = [data.get("printer_key") or "default"]
            keys = [str(k) for k in keys]
            return {"jobs": env["label.print.job"]._agent_pull(keys, limit)}
        return self._run(handler)

    @http.route("/label_tspl/ack", type="http", auth="none", methods=["POST"], csrf=False)
    def ack(self, **kw):
        def handler(env, data):
            return {"ok": True,
                    "updated": env["label.print.job"]._agent_ack(data.get("results") or [])}
        return self._run(handler)
