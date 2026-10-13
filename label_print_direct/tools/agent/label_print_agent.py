# -*- coding: utf-8 -*-
"""Etiket çap agenti — printerin qoşulduğu kompüterdə işləyən körpü.

Dövrə: hər N saniyədə Odoo-dan gözləyən işləri çəkir, printerə xam yazır,
nəticəni bildirir. Odoo agentə heç vaxt müraciət etmir (port açmaq lazım deyil).

İstifadə:
    LabelPrintAgent.exe                     pəncərə ilə (config.ini exe-nin yanındadır)
    python label_print_agent.py             pəncərə ilə (customtkinter + pywin32 lazımdır)
    python label_print_agent.py --console   pəncərəsiz, servis/konsol üçün
    ... [--console] C:\\yol\\config.ini      başqa config faylı

Printer adı "tcp://192.168.1.50:9100" formasında olsa, şəbəkə printerinə birbaşa
socket ilə yazılır. Adın qabağında "escpos:" olsa (çek printeri, məs. XP-80C),
TSPL bitmap ESC/POS rasterə çevrilib göndərilir (Odoo-da printerin dili ESC/POS
seçilibsə iş onsuz da hazır gəlir, çevrilmə lazım olmur).
"""
import base64
import configparser
import http.cookiejar
import json
import logging
import logging.handlers
import os
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

APP_NAME = "Etiket çap agenti"
VERSION = "1.2"
log = logging.getLogger("label_print_agent")

DEFAULT_CFG = {
    "server_url": "http://localhost:8069",
    "token": "",
    "database": "",
    "printer_map": {"default": ""},
    "poll_interval": 3.0,
    "start_minimized": False,
}

# Sessiya çərəzi: serverdə bir neçə baza olanda Odoo sorğunu bazaya yalnız
# çərəzlə yönləndirir. GET /web/login?db=<baza> anonim sessiyanı bazaya bağlayır.
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def app_dir():
    """config.ini və agent.log-un qovluğu: exe-nin (və ya skriptin) yanı."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """PyInstaller-ə qoşulmuş fayl (ikon)."""
    base = getattr(sys, "_MEIPASS", app_dir())
    return os.path.join(base, name)


# --------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------- #
def load_config(path):
    cp = configparser.ConfigParser(interpolation=None)
    if not cp.read(path, encoding="utf-8"):
        raise SystemExit("Config tapılmadı: %s" % path)
    a = cp["agent"]
    printer_map = {}
    raw_map = a.get("printer_map", "").strip()
    if raw_map:
        printer_map = json.loads(raw_map)
    else:
        printer_map[a.get("printer_key", "default")] = a.get("printer_name", "")
    return {
        "server_url": a.get("server_url", DEFAULT_CFG["server_url"]).rstrip("/"),
        "token": a.get("token", ""),
        "database": a.get("database", ""),
        "printer_map": printer_map,
        "poll_interval": a.getfloat("poll_interval", 3.0),
        "start_minimized": a.getboolean("start_minimized", False),
    }


def save_config(path, cfg):
    cp = configparser.ConfigParser(interpolation=None)
    cp["agent"] = {
        "server_url": cfg["server_url"],
        "token": cfg["token"],
        "database": cfg["database"],
        "printer_map": json.dumps(cfg["printer_map"], ensure_ascii=False),
        "poll_interval": str(cfg["poll_interval"]),
        "start_minimized": "true" if cfg.get("start_minimized") else "false",
    }
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)


# --------------------------------------------------------------------- #
#  Odoo ilə danışıq
# --------------------------------------------------------------------- #
def bind_database(cfg):
    if not cfg["database"]:
        return
    try:
        with _opener.open(cfg["server_url"] + "/web/login?db=" + cfg["database"], timeout=30):
            pass
    except Exception as e:
        log.warning("Baza sessiyası qurulmadı: %s", e)


def http_post(cfg, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(cfg["server_url"] + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if cfg["database"]:
        req.add_header("X-Odoo-Database", cfg["database"])
    try:
        with _opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:                      # sessiya bazaya bağlı deyil — bağla və təkrar et
            bind_database(cfg)
            with _opener.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        if e.code == 403:
            raise RuntimeError("Token qəbul edilmədi (403). Odoo → Etiket çapı → Konfiqurator → Ayarlar-dakı tokeni yoxlayın.")
        raise


def _body(cfg, **extra):
    d = {"token": cfg["token"], "database": cfg["database"]}
    d.update(extra)
    return d


# --------------------------------------------------------------------- #
#  Printerə yazma
# --------------------------------------------------------------------- #
def payload_bytes(payload):
    if payload.startswith("#B64#"):
        return base64.b64decode(payload[5:])
    return payload.encode("latin-1", "replace")


def tspl_to_escpos(raw):
    """TSPL BITMAP işini ESC/POS raster (GS v 0) əmrinə çevirir.
    TSPL-də 1 bit = ağ, ESC/POS-da 1 bit = qara — baytlar tərsinə çevrilir.
    BITMAP yoxdursa (iş artıq ESC/POS-dursa) olduğu kimi qaytarır."""
    i = raw.find(b"BITMAP ")
    if i < 0:
        return raw
    parts = []
    pos = i + 7
    for _ in range(5):                              # x, y, wb, h, mode
        k = raw.index(b",", pos)
        parts.append(int(raw[pos:k]))
        pos = k + 1
    wb, h = parts[2], parts[3]
    data = raw[pos:pos + wb * h]
    copies = 1
    p = raw.find(b"PRINT ", pos + wb * h)
    if p >= 0:
        num = b""
        for ch in raw[p + 6:p + 12]:
            if not chr(ch).isdigit():
                break
            num += bytes([ch])
        copies = max(1, int(num or b"1"))
    inverted = bytes((~b) & 0xFF for b in data)
    one = (b"\x1b@" + b"\x1ba\x00"
           + b"\x1dv0\x00" + bytes([wb & 0xFF, wb >> 8, h & 0xFF, h >> 8]) + inverted
           + b"\x1bd\x03" + b"\x1dV\x42\x00")
    return one * copies


def write_raw(printer_name, raw):
    if printer_name.startswith("escpos:"):
        printer_name = printer_name[7:]
        raw = tspl_to_escpos(raw)
    if not printer_name:
        raise RuntimeError("printer seçilməyib")
    if printer_name.startswith("tcp://"):
        host, _, port = printer_name[6:].partition(":")
        with socket.create_connection((host, int(port or 9100)), timeout=15) as s:
            s.sendall(raw)
        return
    import win32print  # pywin32
    h = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(h, 1, ("Label", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, raw)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def list_windows_printers():
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]
    except Exception:
        return []


# --------------------------------------------------------------------- #
#  Windows ilə başlat (Startup qovluğunda qısayol)
# --------------------------------------------------------------------- #
def startup_shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup", "Etiket cap agenti.lnk")


def autostart_enabled():
    return os.path.exists(startup_shortcut_path())


def set_autostart(enable):
    path = startup_shortcut_path()
    if not enable:
        if os.path.exists(path):
            os.remove(path)
        return
    import win32com.client  # pywin32
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(path)
    if getattr(sys, "frozen", False):
        sc.TargetPath = sys.executable
        sc.Arguments = ""
    else:
        sc.TargetPath = sys.executable.replace("python.exe", "pythonw.exe")
        sc.Arguments = '"%s"' % os.path.abspath(__file__)
    sc.WorkingDirectory = app_dir()
    sc.Save()


# --------------------------------------------------------------------- #
#  Bir dövrə
# --------------------------------------------------------------------- #
def process_once(cfg):
    res = http_post(cfg, "/label_tspl/pull",
                    _body(cfg, printer_keys=list(cfg["printer_map"].keys()), limit=20))
    if "error" in res:
        log.error("Server: %s", res)
        return 0
    jobs = res.get("jobs") or []
    results = []
    for job in jobs:
        key = job.get("printer_key") or "default"
        printer = cfg["printer_map"].get(key)
        try:
            if not printer:
                raise RuntimeError("printer xəritəsində '%s' açarı yoxdur" % key)
            write_raw(printer, payload_bytes(job.get("payload") or ""))
            results.append({"id": job["id"], "success": True})
            log.info("Çap olundu: %s (%s nüsxə) -> %s", job.get("name"), job.get("copies", 1), printer)
        except Exception as e:
            results.append({"id": job["id"], "success": False, "error": str(e)[:500]})
            log.error("Xəta %s: %s", job.get("name"), e)
    if results:
        http_post(cfg, "/label_tspl/ack", _body(cfg, results=results))
    return len(jobs)


def do_ping(cfg):
    bind_database(cfg)
    res = http_post(cfg, "/label_tspl/ping", _body(cfg))
    if res.get("ok"):
        log.info("Server cavab verdi: %s", res.get("server"))
    else:
        log.error("Ping: %s", res)
    return res


def do_test_print(cfg, key):
    printer = cfg["printer_map"].get(key)
    if not printer:
        raise RuntimeError("'%s' açarı üçün printer seçilməyib" % key)
    bind_database(cfg)
    res = http_post(cfg, "/label_tspl/test", _body(cfg, printer_key=key))
    if "error" in res:
        raise RuntimeError("Server: %s" % res)
    write_raw(printer, payload_bytes(res.get("payload") or ""))
    log.info("Test etiketi çap olundu: format «%s» -> %s", res.get("format"), printer)


class AgentWorker(threading.Thread):
    """Arxa planda dövrə: pull → çap → ack."""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self._stop = threading.Event()

    def run(self):
        log.info("Agent başladı: %s, baza %s, printerlər %s",
                 self.cfg["server_url"], self.cfg["database"] or "-", self.cfg["printer_map"])
        try:
            do_ping(self.cfg)
        except Exception as e:
            log.warning("Ping alınmadı: %s", e)
        while not self._stop.is_set():
            try:
                process_once(self.cfg)
            except Exception as e:
                log.error("Dövrə xətası: %s", e)
            self._stop.wait(max(1.0, float(self.cfg["poll_interval"])))
        log.info("Agent dayandırıldı.")

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------- #
#  Pəncərə (customtkinter)
# --------------------------------------------------------------------- #
def run_gui(config_path):
    import tkinter as tk
    from tkinter import messagebox

    import customtkinter as ctk

    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    C_GREEN, C_GREEN_H = "#16a34a", "#15803d"
    C_RED, C_RED_H = "#dc2626", "#b91c1c"
    C_MUTED = "#6b7280"
    C_CARD = "#ffffff"
    C_BG = "#f3f4f6"
    FONT = "Segoe UI"

    class App(ctk.CTk):
        def __init__(self):
            super().__init__(fg_color=C_BG)
            self.title(APP_NAME)
            self.geometry("900x820")
            self.minsize(800, 700)
            try:
                self.iconbitmap(resource_path("agent.ico"))
            except Exception:
                pass
            self.worker = None
            self.rows = []
            self.printers = list_windows_printers()
            self.log_queue = queue.Queue()
            handler = logging.handlers.QueueHandler(self.log_queue)
            handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
            log.addHandler(handler)

            if os.path.exists(config_path):
                try:
                    cfg = load_config(config_path)
                except Exception as e:
                    messagebox.showerror("Config", "config.ini oxunmadı: %s" % e)
                    cfg = dict(DEFAULT_CFG, printer_map={"default": ""})
            else:
                cfg = dict(DEFAULT_CFG, printer_map={"default": ""})

            self.f_title = ctk.CTkFont(family=FONT, size=22, weight="bold")
            self.f_h = ctk.CTkFont(family=FONT, size=14, weight="bold")
            self.f_body = ctk.CTkFont(family=FONT, size=13)
            self.f_small = ctk.CTkFont(family=FONT, size=11)
            self.f_mono = ctk.CTkFont(family="Consolas", size=12)

            # --- Başlıq ---------------------------------------------------
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.pack(fill="x", padx=20, pady=(16, 8))
            ctk.CTkLabel(head, text=APP_NAME, font=self.f_title).pack(side="left")
            ctk.CTkLabel(head, text="  Odoo → printer körpüsü · v%s" % VERSION, font=self.f_small,
                         text_color=C_MUTED).pack(side="left", pady=(8, 0))
            self.status = ctk.CTkLabel(head, text="●  Dayandırılıb", font=self.f_h, corner_radius=14,
                                       fg_color="#fee2e2", text_color="#991b1b", padx=14, pady=4)
            self.status.pack(side="right")

            body = ctk.CTkFrame(self, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=20, pady=(0, 12))

            # --- Server kartı --------------------------------------------
            card = self._card(body, "Odoo serveri")
            grid = ctk.CTkFrame(card, fg_color="transparent")
            grid.pack(fill="x", padx=14, pady=(0, 12))
            grid.columnconfigure(1, weight=1)
            self.v_url = tk.StringVar(value=cfg["server_url"])
            self.v_db = tk.StringVar(value=cfg["database"])
            self.v_token = tk.StringVar(value=cfg["token"])
            self.v_int = tk.StringVar(value=str(cfg["poll_interval"]).rstrip("0").rstrip("."))
            self._row(grid, 0, "Server ünvanı", ctk.CTkEntry(grid, textvariable=self.v_url, font=self.f_body,
                                                            placeholder_text="http://192.168.1.10:8069"))
            self._row(grid, 1, "Baza", ctk.CTkEntry(grid, textvariable=self.v_db, font=self.f_body,
                                                    placeholder_text="AtomicTest"))
            tokrow = ctk.CTkFrame(grid, fg_color="transparent")
            tokrow.columnconfigure(0, weight=1)
            self.e_token = ctk.CTkEntry(tokrow, textvariable=self.v_token, font=self.f_body, show="•",
                                        placeholder_text="Odoo → Etiket çapı → Konfiqurator → Ayarlar")
            self.e_token.grid(row=0, column=0, sticky="ew")
            self.v_show = tk.BooleanVar(value=False)
            ctk.CTkCheckBox(tokrow, text="göstər", variable=self.v_show, font=self.f_small, width=80,
                            command=lambda: self.e_token.configure(show="" if self.v_show.get() else "•")
                            ).grid(row=0, column=1, padx=(10, 0))
            self._row(grid, 2, "Token", tokrow)
            introw = ctk.CTkFrame(grid, fg_color="transparent")
            ctk.CTkEntry(introw, textvariable=self.v_int, font=self.f_body, width=70).pack(side="left")
            ctk.CTkLabel(introw, text="saniyədə bir növbə yoxlanır", font=self.f_small,
                         text_color=C_MUTED).pack(side="left", padx=(8, 0))
            self._row(grid, 3, "Sorğu intervalı", introw)

            # --- Printerlər kartı ----------------------------------------
            card = self._card(body, "Printerlər", "Odoo-dakı «Printer açarı» → bu kompüterdəki printer")
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=14)
            ctk.CTkLabel(hdr, text="Odoo açarı", font=self.f_small, text_color=C_MUTED, width=150,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(hdr, text="Windows printeri  (və ya tcp://IP:9100)", font=self.f_small,
                         text_color=C_MUTED, anchor="w").pack(side="left", padx=(8, 0))
            ctk.CTkLabel(hdr, text="ESC/POS çek printeri", font=self.f_small, text_color=C_MUTED
                         ).pack(side="right", padx=(0, 52))
            self.rows_frame = ctk.CTkScrollableFrame(card, fg_color="transparent", height=96)
            self.rows_frame.pack(fill="x", padx=8)
            for key, printer in (cfg["printer_map"] or {"default": ""}).items():
                escpos = printer.startswith("escpos:")
                self.add_row(key, printer[7:] if escpos else printer, escpos)
            ctk.CTkButton(card, text="＋  printer əlavə et", font=self.f_small, width=150, height=28,
                          fg_color="transparent", text_color="#1d4ed8", hover_color="#dbeafe",
                          command=lambda: self.add_row("", "", False)).pack(anchor="w", padx=10, pady=(2, 10))

            # --- Düymələr ------------------------------------------------
            bar = ctk.CTkFrame(body, fg_color="transparent")
            bar.pack(fill="x", pady=(6, 6))
            ctk.CTkButton(bar, text="Yadda saxla", font=self.f_body, width=120, command=self.save).pack(side="left")
            ctk.CTkButton(bar, text="Ping", font=self.f_body, width=80, fg_color="#e5e7eb", text_color="#111827",
                          hover_color="#d1d5db", command=self.ping).pack(side="left", padx=(8, 0))
            ctk.CTkButton(bar, text="Test çapı", font=self.f_body, width=100, fg_color="#e5e7eb",
                          text_color="#111827", hover_color="#d1d5db", command=self.test_print).pack(side="left", padx=(8, 0))
            self.v_test_key = tk.StringVar()
            self.om_test_key = ctk.CTkOptionMenu(bar, variable=self.v_test_key, values=[""], width=130,
                                                 font=self.f_body, fg_color="#e5e7eb", text_color="#111827",
                                                 button_color="#d1d5db", button_hover_color="#9ca3af")
            self.om_test_key.pack(side="left", padx=(6, 0))
            self.b_stop = ctk.CTkButton(bar, text="■  Dayandır", font=self.f_body, width=120, fg_color=C_RED,
                                        hover_color=C_RED_H, state="disabled", command=self.stop)
            self.b_stop.pack(side="right")
            self.b_start = ctk.CTkButton(bar, text="▶  Başlat", font=self.f_body, width=120, fg_color=C_GREEN,
                                         hover_color=C_GREEN_H, command=self.start)
            self.b_start.pack(side="right", padx=(0, 8))
            opts = ctk.CTkFrame(body, fg_color="transparent")
            opts.pack(fill="x", pady=(0, 6))
            self.v_autostart = tk.BooleanVar(value=autostart_enabled())
            ctk.CTkCheckBox(opts, text="Windows ilə başlat", variable=self.v_autostart, font=self.f_small,
                            command=self.toggle_autostart).pack(side="left")
            self.v_minimized = tk.BooleanVar(value=bool(cfg.get("start_minimized")))
            ctk.CTkCheckBox(opts, text="Tepsidə başla (pəncərəsiz)", variable=self.v_minimized, font=self.f_small,
                            command=lambda: self.save(quiet=True)).pack(side="left", padx=(16, 0))
            ctk.CTkLabel(opts, text="Pəncərəni bağlamaq agenti dayandırmır — saatın yanındakı ikona yığılır.",
                         font=self.f_small, text_color=C_MUTED).pack(side="left", padx=(16, 0))

            # --- Log -----------------------------------------------------
            card = self._card(body, "Log", expand=True)
            self.txt = ctk.CTkTextbox(card, font=self.f_mono, fg_color="#0f172a", text_color="#e2e8f0",
                                      corner_radius=8, state="disabled", wrap="word", height=200)
            self.txt.pack(fill="both", expand=True, padx=14, pady=(0, 12))
            self.txt.tag_config("err", foreground="#f87171")
            self.txt.tag_config("warn", foreground="#fbbf24")
            self.txt.tag_config("ok", foreground="#4ade80")

            ctk.CTkLabel(self, text="config: %s" % config_path, font=self.f_small, text_color=C_MUTED
                         ).pack(anchor="w", padx=22, pady=(0, 8))

            self.refresh_test_keys()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(200, self.drain_log)
            self.tray = None
            self._tray_hint_shown = False
            self._setup_tray()
            if cfg["token"]:
                self.start()
            if (cfg.get("start_minimized") or "--tray" in sys.argv) and self.tray:
                self.after(300, self.withdraw)

        # --- tepsi (tray) ---------------------------------------------------
        def _setup_tray(self):
            try:
                import pystray
                from PIL import Image
                try:
                    img = Image.open(resource_path("agent.ico"))
                    img.load()
                except Exception:
                    img = Image.new("RGB", (64, 64), "#1f2937")
                menu = pystray.Menu(
                    pystray.MenuItem("Pəncərəni aç", self._tray_show, default=True),
                    pystray.MenuItem(lambda item: "Dayandır" if self._running() else "Başlat", self._tray_toggle),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Çıx", self._tray_quit),
                )
                self.tray = pystray.Icon("label_print_agent", img, APP_NAME, menu)
                self.tray.run_detached()
            except Exception as e:
                self.tray = None
                log.warning("Tepsi ikonu qurulmadı (%s) — pəncərə bağlananda agent dayanacaq.", e)

        def _running(self):
            return bool(self.worker and self.worker.is_alive())

        def _tray_show(self, icon=None, item=None):
            self.after(0, self.show_window)

        def _tray_toggle(self, icon=None, item=None):
            self.after(0, lambda: self.stop() if self._running() else self.start())

        def _tray_quit(self, icon=None, item=None):
            self.after(0, self.quit_app)

        def show_window(self):
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            self.focus_force()

        def _update_tray_title(self):
            if self.tray:
                try:
                    self.tray.title = "%s — %s" % (APP_NAME, "işləyir" if self._running() else "dayandırılıb")
                except Exception:
                    pass

        # --- köməkçilər ---------------------------------------------------
        def _card(self, master, title, subtitle=None, expand=False):
            card = ctk.CTkFrame(master, fg_color=C_CARD, corner_radius=12)
            card.pack(fill="both" if expand else "x", expand=expand, pady=(0, 10))
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(10, 6))
            ctk.CTkLabel(top, text=title, font=self.f_h, anchor="w").pack(side="left")
            if subtitle:
                ctk.CTkLabel(top, text=subtitle, font=self.f_small, text_color=C_MUTED).pack(side="left", padx=(10, 0))
            return card

        def _row(self, grid, r, label, widget):
            ctk.CTkLabel(grid, text=label, font=self.f_body, width=130, anchor="w").grid(row=r, column=0, sticky="w", pady=4)
            widget.grid(row=r, column=1, sticky="ew", pady=4)

        # --- printer sətirləri -------------------------------------------
        def add_row(self, key, printer, escpos):
            fr = ctk.CTkFrame(self.rows_frame, fg_color="transparent")
            fr.pack(fill="x", pady=2)
            v_key = tk.StringVar(value=key)
            v_pr = tk.StringVar(value=printer)
            v_esc = tk.BooleanVar(value=escpos)
            ctk.CTkEntry(fr, textvariable=v_key, font=self.f_body, width=150, placeholder_text="default").pack(side="left")
            ctk.CTkComboBox(fr, variable=v_pr, values=self.printers or [""], font=self.f_body
                            ).pack(side="left", fill="x", expand=True, padx=(8, 8))
            row = {"frame": fr, "key": v_key, "printer": v_pr, "escpos": v_esc}
            ctk.CTkButton(fr, text="✕", width=32, height=28, fg_color="transparent", text_color="#9ca3af",
                          hover_color="#fee2e2", command=lambda: self.remove_row(row)).pack(side="right")
            ctk.CTkSwitch(fr, text="", variable=v_esc, width=44).pack(side="right", padx=(0, 60))
            self.rows.append(row)
            v_key.trace_add("write", lambda *_: self.refresh_test_keys())
            self.refresh_test_keys()

        def remove_row(self, row):
            if len(self.rows) <= 1:
                return
            row["frame"].destroy()
            self.rows.remove(row)
            self.refresh_test_keys()

        def refresh_test_keys(self):
            if not hasattr(self, "om_test_key"):
                return
            keys = [r["key"].get().strip() for r in self.rows if r["key"].get().strip()] or [""]
            self.om_test_key.configure(values=keys)
            if self.v_test_key.get() not in keys:
                self.v_test_key.set(keys[0])

        # --- config ---------------------------------------------------------
        def collect(self):
            pm = {}
            for r in self.rows:
                key = r["key"].get().strip()
                printer = r["printer"].get().strip()
                if not key:
                    continue
                pm[key] = ("escpos:" + printer) if r["escpos"].get() else printer
            try:
                interval = max(1.0, float(self.v_int.get() or 3))
            except ValueError:
                interval = 3.0
            return {
                "server_url": self.v_url.get().strip().rstrip("/") or DEFAULT_CFG["server_url"],
                "token": self.v_token.get().strip(),
                "database": self.v_db.get().strip(),
                "printer_map": pm or {"default": ""},
                "poll_interval": interval,
                "start_minimized": bool(getattr(self, "v_minimized", None) and self.v_minimized.get()),
            }

        def save(self, quiet=False):
            cfg = self.collect()
            try:
                save_config(config_path, cfg)
                if not quiet:
                    log.info("Config saxlanıldı: %s", config_path)
            except Exception as e:
                messagebox.showerror("Config", "Saxlanmadı: %s" % e)
            return cfg

        # --- əməliyyatlar ---------------------------------------------------
        def _in_thread(self, fn):
            threading.Thread(target=fn, daemon=True).start()

        def ping(self):
            cfg = self.save(quiet=True)

            def job():
                try:
                    do_ping(cfg)
                except Exception as e:
                    log.error("Ping alınmadı: %s", e)
            self._in_thread(job)

        def test_print(self):
            cfg = self.save(quiet=True)
            key = self.v_test_key.get()

            def job():
                try:
                    do_test_print(cfg, key)
                except Exception as e:
                    log.error("Test çapı alınmadı: %s", e)
            self._in_thread(job)

        def toggle_autostart(self):
            try:
                set_autostart(self.v_autostart.get())
                log.info("Windows ilə başlat: %s", "aktiv" if self.v_autostart.get() else "söndürüldü")
            except Exception as e:
                log.error("Avtostart qurulmadı: %s", e)
                self.v_autostart.set(autostart_enabled())

        def start(self):
            cfg = self.save(quiet=True)
            if not cfg["token"]:
                messagebox.showwarning("Token", "Token boşdur. Odoo → Etiket çapı → Konfiqurator → Ayarlar-dan köçürün.")
                return
            if self.worker and self.worker.is_alive():
                return
            self.worker = AgentWorker(cfg)
            self.worker.start()
            self.status.configure(text="●  İşləyir", fg_color="#dcfce7", text_color="#166534")
            self.b_start.configure(state="disabled")
            self.b_stop.configure(state="normal")
            self._update_tray_title()

        def stop(self):
            if self.worker:
                self.worker.stop()
                self.worker = None
            self.status.configure(text="●  Dayandırılıb", fg_color="#fee2e2", text_color="#991b1b")
            self.b_start.configure(state="normal")
            self.b_stop.configure(state="disabled")
            self._update_tray_title()

        def drain_log(self):
            recs = []
            try:
                while True:
                    recs.append(self.log_queue.get_nowait())
            except queue.Empty:
                pass
            if recs:
                self.txt.configure(state="normal")
                for rec in recs:
                    msg = rec.getMessage()
                    tag = ("err" if rec.levelno >= logging.ERROR else "warn" if rec.levelno >= logging.WARNING
                           else "ok" if "Çap olundu" in msg or "cavab verdi" in msg else None)
                    self.txt.insert("end", msg + "\n", tag)
                n = int(self.txt.index("end-1c").split(".")[0])
                if n > 2000:
                    self.txt.delete("1.0", "%d.0" % (n - 2000))
                self.txt.see("end")
                self.txt.configure(state="disabled")
            self.after(200, self.drain_log)

        def on_close(self):
            """X düyməsi: tepsi varsa pəncərə gizlənir, agent işləyir; yoxdursa çıxır."""
            if not self.tray:
                self.quit_app()
                return
            self.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                try:
                    self.tray.notify("Agent tepsidə işləməyə davam edir. Açmaq üçün ikona klikləyin.", APP_NAME)
                except Exception:
                    pass
            log.info("Pəncərə tepsiyə yığıldı, agent işləyir.")

        def quit_app(self):
            self.stop()
            if self.tray:
                try:
                    self.tray.stop()
                except Exception:
                    pass
                self.tray = None
            self.destroy()

    App().mainloop()


# --------------------------------------------------------------------- #
#  Konsol rejimi
# --------------------------------------------------------------------- #
def run_console(config_path):
    cfg = load_config(config_path)
    worker = AgentWorker(cfg)
    worker.start()
    try:
        while worker.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    console = "--console" in sys.argv[1:]
    here = app_dir()
    config_path = args[0] if args else os.path.join(here, "config.ini")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        fh = logging.FileHandler(os.path.join(here, "agent.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except Exception:
        pass
    if console:
        run_console(config_path)
        return
    try:
        run_gui(config_path)
    except ImportError as e:
        log.warning("Pəncərə açılmadı (%s), konsol rejimində işləyir", e)
        run_console(config_path)


if __name__ == "__main__":
    main()
