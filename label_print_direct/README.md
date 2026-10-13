# Etiket Çapı (birbaşa) — Odoo 18

Vizual etiket dizayneri + termal etiket printerinə birbaşa çap (TSPL bitmap, agent körpüsü).

## Necə işləyir

1. **Etiket çapı → Formatlar**: etiketin ölçüsünü yaz, «Standart dizayn» bas, dizaynerdə
   elementləri sürüşdür. Sağdakı önizləmə serverdə çəkilir — printerdə çıxan şəkil budur.
2. **Məhsul → Print Labels** (məhsul siyahısı, məhsul formu, anbar qəbulu — hamısı standart
   pəncərə): «Etiket printeri (birbaşa)» seç → formatı seç → Confirm. Hər məhsul üçün
   **Çap növbəsi**nə bir iş düşür (`pending`).
3. **Agent** (`tools/agent/label_print_agent.py`) printerin qoşulduğu kompüterdə işləyir:
   hər 3 saniyədə `/label_tspl/pull` ilə işləri çəkir, printerə RAW yazır, `/label_tspl/ack`
   ilə nəticəni bildirir. Token **Etiket çapı → Ayarlar**dadır.
   Pəncərə ilə açılır: server, baza, token, printer xəritəsi (Windows printer siyahısından),
   ESC/POS açarı, Ping, Test çapı, Başlat/Dayandır, «Windows ilə başlat», rəngli canlı log.
   Token varsa açılan kimi özü başlayır. Pəncərəni bağlamaq agenti dayandırmır — saatın
   yanındakı ikona yığılır (klik = pəncərə, sağ klik = Başlat/Dayandır/Çıx); «Tepsidə başla»
   seçimi ilə pəncərəsiz qalxır. `--console` ilə pəncərəsiz (servis üçün).
   **Exe**: `tools/agent/build_exe.bat` (PyInstaller, `pip install pywin32 customtkinter pyinstaller`)
   → `dist/LabelPrintAgent.exe`; müştəriyə yalnız exe verilir, `config.ini` və `agent.log`
   exe-nin yanında yaranır. Skript kimi işlətmək üçün: Python 3 + `pywin32` + `customtkinter`.

## Agent API (sadə JSON, `auth=none`, token ilə)

| Yol | Body | Cavab |
|---|---|---|
| `POST /label_tspl/ping` | `{token, database}` | `{ok, server}` |
| `POST /label_tspl/test` | `{token, database, printer_key}` | `{payload, format}` |
| `POST /label_tspl/pull` | `{token, database, printer_keys:[…], limit}` | `{jobs:[{id, name, payload, copies, printer_key}]}` |
| `POST /label_tspl/ack` | `{token, database, results:[{id, success, error}]}` | `{ok, updated}` |

`payload` `#B64#` ilə başlayırsa base64-dür (xam TSPL + bitmap baytları); agent açıb olduğu
kimi printerə yazır.

**Bir neçə bazalı server:** Odoo sessiyasız sorğunu bazaya yönləndirmir (yalnız bir baza olanda
avtomatik tapır). Agent əvvəlcə `GET /web/login?db=<baza>` edib aldığı `session_id` çərəzini
sonrakı sorğularda göndərir — `label_print_agent.py` bunu özü edir. Tək bazalı serverdə lazım deyil.

## Element JSON-u (`label.format.layout_json`)

`type` (name, name_plain, code, barcode, barcode_text, price, currency, text, field, line, rect),
`x_mm`, `y_mm`, `width_mm` (mətn üçün 0 = kənara qədər), `height_mm`, `font_pt`, `bold`, `align`,
`rotation` (0/90/180/270, saat istiqamətində; x,y = fırlanmış çərçivənin yuxarı-sol küncü),
`wrap`, `wrap_lines`, `text`, `field_name`, `show_barcode_text`, `barcode_symbology`
(code128 | ean13), `module_width`, `price_amount_only`, `thickness_mm`.

## Asılılıqlar

Python: `Pillow`, `reportlab` (Odoo ilə gəlir). Şrift: `static/fonts/NotoSans-Regular.ttf` (OFL).
