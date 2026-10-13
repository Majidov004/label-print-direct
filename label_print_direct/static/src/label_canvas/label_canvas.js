/** @odoo-module **/
/*
 * Etiket dizayneri — «label.format.layout_json» sahəsi üçün widget.
 *
 * Elementlər mm koordinatı ilə SVG kanvasda göstərilir, sürüşdürülüb ölçüsü
 * dəyişdirilir; hər dəyişiklik JSON kimi qeydə yazılır (record.update).
 * Sağdakı «Çap önizləməsi» serverdə eyni render yolu ilə çəkilmiş PNG-dir —
 * printerdə çıxacaq şəkil budur.
 */
import { Component, onWillRender, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { debounce } from "@web/core/utils/timing";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const TYPES = [
    { type: "name", label: "Məhsul adı", icon: "fa-tag" },
    { type: "name_plain", label: "Ad (kodsuz)", icon: "fa-tag" },
    { type: "code", label: "Daxili kod", icon: "fa-hashtag" },
    { type: "barcode", label: "Barkod", icon: "fa-barcode" },
    { type: "barcode_text", label: "Barkod nömrəsi", icon: "fa-sort-numeric-asc" },
    { type: "price", label: "Qiymət", icon: "fa-money" },
    { type: "currency", label: "Valyuta", icon: "fa-usd" },
    { type: "text", label: "Sabit mətn", icon: "fa-font" },
    { type: "field", label: "Sahə (modul)", icon: "fa-database" },
    { type: "line", label: "Xətt", icon: "fa-minus" },
    { type: "rect", label: "Çərçivə", icon: "fa-square-o" },
];
const TEXT_TYPES = new Set(["name", "name_plain", "code", "barcode_text", "price", "currency", "text", "field"]);
const SAMPLES = {
    name: "[REF-001] Nümunə məhsul adı",
    name_plain: "Nümunə məhsul adı",
    code: "REF-001",
    barcode_text: "2000000000002",
    currency: "₼",
};
// Kanvasda barkodun təxmini görünüşü üçün sabit zolaq nümunəsi (real barkod önizləmədədir).
const BAR_PATTERN = "21122222212222222212122312132213122212221313221221213222311"
    .split("").map(Number);
const PT_TO_MM = 25.4 / 72;
const ZOOM_MIN = 4;
const ZOOM_MAX = 16;

let uidSeq = 1;

function round2(v) {
    return Math.round(v * 100) / 100;
}
function snap(v, step = 0.5) {
    return Math.round(v / step) * step;
}
function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
}

function newElement(type, x, y) {
    const isShape = type === "line" || type === "rect";
    return {
        uid: uidSeq++,
        id: false,
        sequence: 10,
        type,
        x_mm: x,
        y_mm: y,
        width_mm: type === "barcode" ? 30 : isShape ? 20 : 0,
        height_mm: type === "barcode" ? 12 : type === "line" ? 0.4 : type === "rect" ? 10 : 0,
        font_pt: type === "barcode_text" || type === "barcode" ? 7 : type === "price" ? 11 : 9,
        bold: type === "name" || type === "price",
        align: "left",
        rotation: 0,
        wrap: type === "name" || type === "name_plain",
        wrap_lines: 2,
        text: type === "text" ? "Mətn" : "",
        field_name: "",
        field_source: "product",
        short_name: false,
        show_barcode_text: true,
        barcode_symbology: "code128",
        module_width: 2,
        price_amount_only: false,
        thickness_mm: 0.3,
    };
}

export class LabelCanvasField extends Component {
    static template = "label_print_direct.LabelCanvas";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.rootRef = useRef("root");
        this.TYPES = TYPES;
        this._lastValue = null;
        this._geomKey = "";
        this._usageCodes = null;
        this._pendingCommit = false;
        this._previewToken = 0;
        this._drag = null;
        this.state = useState({
            elements: [],
            selected: null,
            guides: [],
            snapEnabled: true,
            zoom: 8,
            showPreview: true,
            preview: null,
            previewError: "",
            fieldsList: [],
        });
        this.refreshPreview = debounce(() => this._refreshPreview(), 400);

        onWillStart(() => this.loadFields());
        onWillRender(() => this.syncFromRecord());
        onWillUnmount(() => {
            this.refreshPreview.cancel();
            this.stopDrag();
        });
    }

    // ------------------------------------------------------------------ //
    //  Qeyd <-> vəziyyət
    // ------------------------------------------------------------------ //
    syncFromRecord() {
        const value = this.props.record.data[this.props.name] || "[]";
        if (value !== this._lastValue) {
            if (this._pendingCommit) {
                // Öz dəyişikliyimiz hələ qeydə çatmayıb — köhnə dəyəri geri oxumayaq.
                return;
            }
            this._lastValue = value;
            if (!this.sameLayout(value)) {
                // Kənardan gələn real dəyişiklik (məs. «Standart dizayn», saxlama):
                // elementləri yenidən qur, seçimi sıra nömrəsi ilə qoruyub saxla.
                const idx = this.state.elements.findIndex((e) => e.uid === this.state.selected);
                this.state.elements = this.parse(value);
                this.state.selected = idx >= 0 && idx < this.state.elements.length
                    ? this.state.elements[idx].uid : null;
                this.refreshPreview();
            }
        }
        const geomKey = `${this.widthMm}|${this.heightMm}|${this.dpi}`;
        if (geomKey !== this._geomKey) {
            this._geomKey = geomKey;
            this.refreshPreview();
        }
        const codes = this.props.record.data.usage_codes || "";
        if (this._usageCodes !== null && codes !== this._usageCodes) {
            this.loadFields();
        }
    }

    /** «Sahə» siyahısı: məhsul + formatın istifadə yerlərinin sənəd/sətir sahələri. */
    async loadFields() {
        const codes = this.props.record.data.usage_codes || "";
        this._usageCodes = codes;
        try {
            const list = await this.orm.silent.call("label.format", "get_field_options",
                [codes ? codes.split(",") : []]);
            if (this._usageCodes === codes) {
                this.state.fieldsList = list;
            }
        } catch {
            this.state.fieldsList = [];
        }
    }

    get fieldGroups() {
        const groups = [];
        for (const f of this.state.fieldsList) {
            let g = groups.find((x) => x.source === f.source && x.group === f.group);
            if (!g) {
                g = { source: f.source, group: f.group, items: [] };
                groups.push(g);
            }
            g.items.push(f);
        }
        return groups;
    }

    onFieldPick(ev) {
        const [source, name] = (ev.target.value || "|").split("|");
        this.setProps({ field_source: source || "product", field_name: name || "" });
    }

    /** Sabit mətnə yer tutucu daxil et: {name}, {price}, {product.categ_id}, {doc.name} … */
    get basePlaceholders() {
        return [
            { token: "{name}", label: "Məhsul adı (kodla)" },
            { token: "{name_plain}", label: "Məhsul adı (kodsuz)" },
            { token: "{code}", label: "Daxili kod" },
            { token: "{barcode}", label: "Barkod" },
            { token: "{price}", label: "Qiymət (valyuta ilə)" },
            { token: "{price_amount}", label: "Qiymət (yalnız məbləğ)" },
            { token: "{currency}", label: "Valyuta" },
        ];
    }
    onInsertPlaceholder(ev) {
        const token = ev.target.value;
        ev.target.value = "";
        const el = this.selectedEl;
        if (!token || !el || el.type !== "text") {
            return;
        }
        const text = el.text || "";
        this.setProps({ text: text + (text && !text.endsWith(" ") ? " " : "") + token });
    }

    parse(value) {
        let arr = [];
        try {
            arr = JSON.parse(value);
        } catch {
            arr = [];
        }
        if (!Array.isArray(arr)) {
            arr = [];
        }
        return arr.map((d) => Object.assign(newElement(d.type || "text", 0, 0), d, {
            uid: uidSeq++,
            rotation: parseInt(d.rotation) || 0,
        }));
    }

    serialize() {
        return this.state.elements.map((e, i) => {
            const { uid, ...rest } = e;
            rest.sequence = (i + 1) * 10;
            return rest;
        });
    }

    /** Qeyddəki JSON hazırkı elementlərlə məzmunca eynidirmi (açar sırası, id, uid nəzərə alınmır). */
    sameLayout(value) {
        let arr;
        try {
            arr = JSON.parse(value);
        } catch {
            return false;
        }
        if (!Array.isArray(arr) || arr.length !== this.state.elements.length) {
            return false;
        }
        const canon = (e) => {
            const o = {};
            for (const k of Object.keys(newElement("text", 0, 0))) {
                if (k === "uid" || k === "id" || k === "sequence") {
                    continue;
                }
                const v = e[k];
                o[k] = typeof v === "number" ? Math.round(v * 1000) / 1000 : v === undefined ? null : v;
            }
            return JSON.stringify(o);
        };
        return arr.every((e, i) => canon(Object.assign(newElement(e.type || "text", 0, 0), e,
            { rotation: parseInt(e.rotation) || 0 })) === canon(this.state.elements[i]));
    }

    commit() {
        this._lastValue = JSON.stringify(this.serialize());
        this._pendingCommit = true;
        const done = () => {
            this._pendingCommit = false;
            const value = this.props.record.data[this.props.name] || "[]";
            if (value !== this._lastValue) {
                this.render();          // qeyd başqa dəyər saxlayıbsa, onu yenidən oxu
            }
        };
        Promise.resolve(this.props.record.update({ [this.props.name]: this._lastValue })).then(done, done);
        this.refreshPreview();
    }

    spec() {
        return {
            width_mm: this.widthMm,
            height_mm: this.heightMm,
            dpi: this.dpi,
            elements: this.serialize(),
        };
    }

    async _refreshPreview() {
        if (!this.state.showPreview) {
            return;
        }
        const token = ++this._previewToken;
        try {
            const url = await this.orm.silent.call("label.format", "preview_from_spec", [this.spec()]);
            if (token !== this._previewToken) {
                return;
            }
            this.state.preview = url;
            this.state.previewError = "";
        } catch {
            if (token !== this._previewToken) {
                return;
            }
            this.state.previewError = "Önizləmə alınmadı (server xətası).";
        }
    }

    // ------------------------------------------------------------------ //
    //  Həndəsə
    // ------------------------------------------------------------------ //
    get widthMm() {
        return Number(this.props.record.data.width_mm) || 40;
    }
    get heightMm() {
        return Number(this.props.record.data.height_mm) || 30;
    }
    get dpi() {
        return Number(this.props.record.data.dpi) || 203;
    }
    get z() {
        return this.state.zoom;
    }
    get svgW() {
        return Math.round(this.widthMm * this.z) + 1;
    }
    get svgH() {
        return Math.round(this.heightMm * this.z) + 1;
    }
    get gridLines() {
        const out = [];
        const z = this.z;
        for (let mm = 5; mm < this.widthMm; mm += 5) {
            out.push({ key: "v" + mm, x1: mm * z, y1: 0, x2: mm * z, y2: this.svgH });
        }
        for (let mm = 5; mm < this.heightMm; mm += 5) {
            out.push({ key: "h" + mm, x1: 0, y1: mm * z, x2: this.svgW, y2: mm * z });
        }
        return out;
    }
    get selectedEl() {
        return this.state.elements.find((e) => e.uid === this.state.selected) || null;
    }

    isTextType(el) {
        return TEXT_TYPES.has(el.type);
    }
    fontPx(el) {
        return (el.font_pt || 9) * PT_TO_MM * this.z;
    }
    typeLabel(type) {
        const t = TYPES.find((x) => x.type === type);
        return t ? t.label : type;
    }
    listHint(el) {
        if (el.type === "text") {
            return el.text || "";
        }
        if (el.type === "field") {
            return el.field_name ? `${el.field_source || "product"}: ${el.field_name}` : "";
        }
        return `${el.x_mm}, ${el.y_mm}`;
    }
    sampleText(el) {
        if (el.type === "text") {
            return el.text || "Mətn";
        }
        if (el.type === "field") {
            return el.field_name ? `{${el.field_name}}` : "{sahə}";
        }
        if (el.type === "price") {
            return el.price_amount_only ? "12.50" : "12.50 ₼";
        }
        return SAMPLES[el.type] || "";
    }
    // Kanvas üçün sadə sətrə keçirmə (simvol sayı ilə); dəqiq nəticə önizləmədədir.
    sampleLines(el, boxW) {
        const text = this.sampleText(el);
        if (!el.wrap) {
            return [text];
        }
        const maxChars = Math.max(1, Math.floor(boxW / (this.fontPx(el) * 0.55)));
        const maxLines = Math.max(1, el.wrap_lines || 2);
        const lines = [];
        let cur = "";
        for (const w of text.split(" ")) {
            const cand = cur ? cur + " " + w : w;
            if (cand.length <= maxChars || !cur) {
                cur = cand;
            } else {
                lines.push(cur);
                cur = w;
                if (lines.length >= maxLines) {
                    cur = "";
                    break;
                }
            }
        }
        if (cur && lines.length < maxLines) {
            lines.push(cur);
        }
        return lines.length ? lines : [text];
    }
    /** Elementin fırlanmadan əvvəlki qutusu (px). */
    elBox(el) {
        const z = this.z;
        if (el.type === "barcode") {
            const wmm = el.width_mm || (123 * (el.module_width || 2) * 25.4) / this.dpi;
            const h = (el.height_mm || 10) * z + (el.show_barcode_text ? this.fontPx(el) * 1.4 : 0);
            return { w: Math.max(4, wmm * z), h: Math.max(4, h) };
        }
        if (el.type === "line" || el.type === "rect") {
            return { w: Math.max(2, (el.width_mm || 10) * z), h: Math.max(1, (el.height_mm || 0.4) * z) };
        }
        const wmm = el.width_mm || Math.max(2, this.widthMm - el.x_mm);
        const w = Math.max(4, wmm * z);
        const lines = this.sampleLines(el, w).length;
        return { w, h: Math.max(4, this.fontPx(el) * 1.2 * lines) };
    }
    elTransform(el) {
        const z = this.z;
        const x = el.x_mm * z;
        const y = el.y_mm * z;
        const b = this.elBox(el);
        switch (el.rotation) {
            case 90:
                return `translate(${x + b.h}, ${y}) rotate(90)`;
            case 180:
                return `translate(${x + b.w}, ${y + b.h}) rotate(180)`;
            case 270:
                return `translate(${x}, ${y + b.w}) rotate(270)`;
            default:
                return `translate(${x}, ${y})`;
        }
    }
    textLines(el, b) {
        const lh = this.fontPx(el) * 1.2;
        const fs = this.fontPx(el);
        const anchor = el.align === "center" ? "middle" : el.align === "right" ? "end" : "start";
        const x = el.align === "center" ? b.w / 2 : el.align === "right" ? b.w : 0;
        return this.sampleLines(el, b.w).map((text, i) => ({ text, x, y: i * lh + fs * 0.85, anchor }));
    }
    barStripes(el, b) {
        const total = BAR_PATTERN.reduce((a, c) => a + c, 0);
        const module = b.w / total;
        const h = (el.height_mm || 10) * this.z;
        const out = [];
        let x = 0;
        BAR_PATTERN.forEach((m, i) => {
            if (i % 2 === 0) {
                out.push({ x, w: Math.max(0.5, m * module), h });
            }
            x += m * module;
        });
        return out;
    }
    barTextY(el, b) {
        return b.h - this.fontPx(el) * 0.3;
    }
    rectStrokePx(el) {
        return Math.max(1, (el.thickness_mm || 0.3) * this.z);
    }

    // ------------------------------------------------------------------ //
    //  Seçim, sürüşdürmə, ölçü
    // ------------------------------------------------------------------ //
    select(uid) {
        this.state.selected = uid;
    }
    onCanvasPointerDown() {
        this.state.selected = null;
    }
    onElementPointerDown(ev, el) {
        if (ev.button !== 0) {
            return;
        }
        this.state.selected = el.uid;
        if (this.rootRef.el) {
            this.rootRef.el.focus({ preventScroll: true });
        }
        if (this.props.readonly) {
            return;
        }
        this.startDrag(ev, el, "move");
    }
    onHandlePointerDown(ev, el) {
        if (this.props.readonly) {
            return;
        }
        this.startDrag(ev, el, "resize");
    }
    startDrag(ev, el, mode) {
        ev.preventDefault();
        const effW = el.width_mm || Math.max(2, this.widthMm - el.x_mm);
        this._drag = { mode, uid: el.uid, sx: ev.clientX, sy: ev.clientY,
                       x: el.x_mm, y: el.y_mm, w: effW, h: el.height_mm, moved: false };
        this._onMove = (e) => this.onDragMove(e);
        this._onUp = () => this.onDragEnd();
        window.addEventListener("pointermove", this._onMove);
        window.addEventListener("pointerup", this._onUp);
    }
    onDragMove(ev) {
        const d = this._drag;
        if (!d) {
            return;
        }
        const el = this.state.elements.find((e) => e.uid === d.uid);
        if (!el) {
            return;
        }
        const pxX = ev.clientX - d.sx;
        const pxY = ev.clientY - d.sy;
        if (!d.moved && Math.abs(pxX) < 3 && Math.abs(pxY) < 3) {
            return;                                    // kliklə təsadüfi tərpənmə olmasın
        }
        const dx = pxX / this.z;
        const dy = pxY / this.z;
        d.moved = true;
        const free = ev.altKey || !this.state.snapEnabled;   // Alt = sərbəst hərəkət
        if (d.mode === "move") {
            if (free) {
                el.x_mm = clamp(round2(d.x + dx), 0, Math.max(0, this.widthMm - 1));
                el.y_mm = clamp(round2(d.y + dy), 0, Math.max(0, this.heightMm - 1));
                this.state.guides = [];
            } else {
                const r = this.alignSnap(el, d.x + dx, d.y + dy);
                el.x_mm = clamp(r.x, 0, Math.max(0, this.widthMm - 1));
                el.y_mm = clamp(r.y, 0, Math.max(0, this.heightMm - 1));
                this.state.guides = r.guides;
            }
        } else {
            const step = free ? 0.1 : 0.5;
            el.width_mm = Math.max(1, snap(d.w + dx, step));
            if (el.type === "barcode" || el.type === "line" || el.type === "rect") {
                el.height_mm = Math.max(0.2, snap(d.h + dy, 0.1));
            }
        }
    }
    onDragEnd() {
        const d = this._drag;
        this.stopDrag();
        this.state.guides = [];
        if (d && d.moved) {
            this.commit();
        }
    }

    /** Elementin etiket üzərindəki (fırlanmış) çərçivəsi, mm. */
    rotBoxMm(el) {
        const b = this.elBox(el);
        const turned = el.rotation === 90 || el.rotation === 270;
        return { w: (turned ? b.h : b.w) / this.z, h: (turned ? b.w : b.h) / this.z };
    }

    /**
     * Sürüşdürərkən bərabərləmə: başqa elementlərin kənarı/mərkəzi, etiketin kənarı
     * və mərkəzi. Tutanda qırıq xətt göstərir; etiketin mərkəzində olanda hər iki
     * kənara qədər xətt çəkir (kənarlardan bərabər məsafə).
     */
    alignSnap(el, x, y) {
        const tol = 3 / this.z;                            // 3 piksel, böyütmədən asılı (mm)
        const W = this.widthMm;
        const H = this.heightMm;
        const box = this.rotBoxMm(el);
        const autoWidth = this.isTextType(el) && !el.width_mm;   // eni kənara qədər olan mətn
        // kind: "edge" = etiket kənarı, "center" = etiket mərkəzi, "el-edge"/"el-center" = başqa element
        const xTargets = [{ v: 0, kind: "edge" }, { v: W, kind: "edge" }, { v: W / 2, kind: "center" }];
        const yTargets = [{ v: 0, kind: "edge" }, { v: H, kind: "edge" }, { v: H / 2, kind: "center" }];
        for (const o of this.state.elements) {
            if (o.uid === el.uid) {
                continue;
            }
            const ob = this.rotBoxMm(o);
            xTargets.push({ v: o.x_mm, kind: "el-edge" }, { v: o.x_mm + ob.w, kind: "el-edge" },
                          { v: o.x_mm + ob.w / 2, kind: "el-center" });
            yTargets.push({ v: o.y_mm, kind: "el-edge" }, { v: o.y_mm + ob.h, kind: "el-edge" },
                          { v: o.y_mm + ob.h / 2, kind: "el-center" });
        }
        const snapAxis = (pos, size, targets, leftOnly) => {
            const edgeProbes = leftOnly ? [0] : [0, size];          // sol/yuxarı, sağ/aşağı
            const centerProbes = leftOnly ? [] : [size / 2];
            let best = null;
            for (const t of targets) {
                const isCenter = t.kind === "center" || t.kind === "el-center";
                for (const off of (isCenter ? centerProbes : edgeProbes)) {   // kənar↔kənar, mərkəz↔mərkəz
                    const dist = Math.abs(pos + off - t.v);
                    if (dist <= tol && (!best || dist < best.dist)) {
                        best = { dist, v: t.v, off, kind: t.kind };
                    }
                }
            }
            return best ? { pos: round2(best.v - best.off), guide: best } : { pos: snap(pos), guide: null };
        };
        const rx = snapAxis(x, box.w, xTargets, autoWidth);
        const ry = snapAxis(y, box.h, yTargets, false);
        const z = this.z;
        const guides = [];
        if (rx.guide) {
            if (rx.guide.kind === "center") {
                const ymid = (ry.pos + box.h / 2) * z;
                guides.push({ x1: 0, y1: ymid, x2: rx.pos * z, y2: ymid, color: "#2563eb" });
                guides.push({ x1: (rx.pos + box.w) * z, y1: ymid, x2: W * z, y2: ymid, color: "#2563eb" });
            } else {
                guides.push({ x1: rx.guide.v * z, y1: 0, x2: rx.guide.v * z, y2: H * z, color: "#e11d48" });
            }
        }
        if (ry.guide) {
            if (ry.guide.kind === "center") {
                const xmid = (rx.pos + box.w / 2) * z;
                guides.push({ x1: xmid, y1: 0, x2: xmid, y2: ry.pos * z, color: "#2563eb" });
                guides.push({ x1: xmid, y1: (ry.pos + box.h) * z, x2: xmid, y2: H * z, color: "#2563eb" });
            } else {
                guides.push({ x1: 0, y1: ry.guide.v * z, x2: W * z, y2: ry.guide.v * z, color: "#e11d48" });
            }
        }
        return { x: rx.pos, y: ry.pos, guides };
    }
    stopDrag() {
        if (this._onMove) {
            window.removeEventListener("pointermove", this._onMove);
            window.removeEventListener("pointerup", this._onUp);
        }
        this._onMove = null;
        this._onUp = null;
        this._drag = null;
    }

    onKeyDown(ev) {
        const tag = (ev.target.tagName || "").toUpperCase();
        if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) {
            return;
        }
        const el = this.selectedEl;
        if (!el || this.props.readonly) {
            return;
        }
        const step = ev.shiftKey ? 2 : 0.5;
        switch (ev.key) {
            case "ArrowLeft":
                el.x_mm = Math.max(0, round2(el.x_mm - step));
                break;
            case "ArrowRight":
                el.x_mm = round2(el.x_mm + step);
                break;
            case "ArrowUp":
                el.y_mm = Math.max(0, round2(el.y_mm - step));
                break;
            case "ArrowDown":
                el.y_mm = round2(el.y_mm + step);
                break;
            case "Delete":
            case "Backspace":
                ev.preventDefault();
                this.removeSelected();
                return;
            default:
                if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "d") {
                    ev.preventDefault();
                    this.duplicateSelected();
                }
                return;
        }
        ev.preventDefault();
        this.commit();
    }

    // ------------------------------------------------------------------ //
    //  Alət paneli və xassələr
    // ------------------------------------------------------------------ //
    addElement(type) {
        if (this.props.readonly) {
            return;
        }
        let y = 2;
        for (const e of this.state.elements) {
            const b = this.elBox(e);
            y = Math.max(y, e.y_mm + b.h / this.z + 1);
        }
        y = clamp(snap(y), 0, Math.max(0, this.heightMm - 4));
        const el = newElement(type, 2, y);
        this.state.elements.push(el);
        this.state.selected = el.uid;
        this.commit();
    }
    removeSelected() {
        const idx = this.state.elements.findIndex((e) => e.uid === this.state.selected);
        if (idx < 0) {
            return;
        }
        this.state.elements.splice(idx, 1);
        this.state.selected = null;
        this.commit();
    }
    duplicateSelected() {
        const el = this.selectedEl;
        if (!el) {
            return;
        }
        const copy = Object.assign({}, el, { uid: uidSeq++, id: false,
            x_mm: round2(el.x_mm + 2), y_mm: round2(el.y_mm + 2) });
        this.state.elements.push(copy);
        this.state.selected = copy.uid;
        this.commit();
    }
    moveSelected(dir) {
        const arr = this.state.elements;
        const idx = arr.findIndex((e) => e.uid === this.state.selected);
        const to = idx + dir;
        if (idx < 0 || to < 0 || to >= arr.length) {
            return;
        }
        const [el] = arr.splice(idx, 1);
        arr.splice(to, 0, el);
        this.commit();
    }
    setZoom(delta) {
        this.state.zoom = clamp(this.state.zoom + delta, ZOOM_MIN, ZOOM_MAX);
    }
    togglePreview(ev) {
        this.state.showPreview = !!ev.target.checked;
        if (this.state.showPreview) {
            this._refreshPreview();
        }
    }
    toggleSnap(ev) {
        this.state.snapEnabled = !!ev.target.checked;
    }

    setProp(key, value) {
        this.setProps({ [key]: value });
    }
    setProps(values) {
        const el = this.selectedEl;
        if (!el || this.props.readonly) {
            return;
        }
        Object.assign(el, values);
        this.commit();
    }
    onNum(key, ev, min = null) {
        let v = parseFloat(ev.target.value);
        if (isNaN(v)) {
            v = 0;
        }
        if (min !== null) {
            v = Math.max(min, v);
        }
        this.setProp(key, round2(v));
    }
    onInt(key, ev, min = 1) {
        let v = parseInt(ev.target.value);
        if (isNaN(v)) {
            v = min;
        }
        this.setProp(key, Math.max(min, v));
    }
    onCheck(key, ev) {
        this.setProp(key, !!ev.target.checked);
    }
    onSelect(key, ev) {
        this.setProp(key, key === "rotation" ? parseInt(ev.target.value) || 0 : ev.target.value);
    }
    onText(key, ev) {
        this.setProp(key, ev.target.value);
    }
}

export const labelCanvasField = {
    component: LabelCanvasField,
    displayName: "Etiket dizayneri",
    supportedTypes: ["text", "char"],
};

registry.category("fields").add("label_canvas", labelCanvasField);
