# -*- coding: utf-8 -*-
{
    "name": "Etiket Çapı (birbaşa)",
    "version": "18.0.1.4.0",
    "summary": "Vizual etiket dizayneri və termal printerə birbaşa çap (TSPL, agent körpüsü)",
    "description": """
Etiket Çapı (birbaşa)
==================
* Sürüşdür-burax etiket dizayneri (mm dəqiqliyi, canlı çap önizləməsi)
* Etiket printerin öz dilində (TSPL bitmap) hazırlanır — Windows drayveri kənarda qalır,
  etiket dizayn edildiyi ölçüdə çıxır
* Çap növbəsi: hər çap bir işdir (gözləyir → göndərildi → çap olundu / xəta)
* Agent API: printerin yanındakı kompüterdə işləyən kiçik agent növbəni çəkib
  USB printerə yazır (yalnız çıxan HTTP, port açmaq lazım deyil)
* Standart «Print Labels» pəncərəsinə «Etiket printeri» seçimi əlavə olunur —
  məhsul, anbar qəbulu və siyahıdan çap eyni yolla gedir
""",
    "author": "H. Məcidov",
    "website": "https://github.com/Majidov004",
    "category": "Inventory",
    "license": "OPL-1",
    "depends": ["product", "stock", "sale", "purchase"],
    "external_dependencies": {"python": ["Pillow", "reportlab"]},
    "data": [
        "security/label_security.xml",
        "security/ir.model.access.csv",
        "data/sequence.xml",
        "data/label_usage_data.xml",
        "data/label_catalog_data.xml",
        "data/actions.xml",
        "views/label_printer_views.xml",
        "views/label_format_views.xml",
        "views/label_print_job_views.xml",
        "views/product_label_layout_views.xml",
        "views/product_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "label_print_direct/static/src/label_canvas/label_canvas.scss",
            "label_print_direct/static/src/label_canvas/label_canvas.xml",
            "label_print_direct/static/src/label_canvas/label_canvas.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
