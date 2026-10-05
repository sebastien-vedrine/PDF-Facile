# -*- coding: utf-8 -*-
"""
PDF Facile — lire, remplir, signer et modifier des PDF, simplement.
Interface entièrement en français, pensée pour être intuitive.

Dépendances : PySide6, PyMuPDF
"""
import datetime
import html
import json
import os
import re
import sys
import traceback

import pymupdf
from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QLibraryInfo, QPoint, QPointF,
                            QRectF, QSettings, QSize, QStandardPaths, Qt, QTimer,
                            QTranslator, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QIcon, QImage, QImageReader, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTransform)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

APP_NAME = "PDF Facile"
APP_VERSION = "1.6"
APP_URL = "https://github.com/sebastien-vedrine/PDF-Facile"
ORG_NAME = "PDFFacile"
MAX_UNDO = 30
PT2PX = 96 / 72          # 100 % = taille réelle sur un écran standard
MARGIN = 24              # marge autour de la page affichée
IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff)"
PDF_FILTER = "Fichiers PDF (*.pdf)"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff")

W_TEXT = pymupdf.PDF_WIDGET_TYPE_TEXT
W_CHECK = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
W_RADIO = pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON
W_COMBO = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
W_LIST = pymupdf.PDF_WIDGET_TYPE_LISTBOX
W_SIGN = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
W_BUTTON = pymupdf.PDF_WIDGET_TYPE_BUTTON
TEXT_LH = 1.17           # hauteur de ligne des textes ajoutés (en × taille)
META_KEY = "PFMeta"      # clé privée qui marque les objets créés par PDF Facile
CHECK_MARKS = [("✓  Coche", "v"), ("✗  Croix", "x")]


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def find_font():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for c in (os.path.join(windir, "Fonts", "arial.ttf"),
              os.path.join(windir, "Fonts", "segoeui.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if os.path.exists(c):
            return c
    return None


FONT_FILE = find_font()
try:
    TEXT_FONT = pymupdf.Font(fontfile=FONT_FILE) if FONT_FILE else pymupdf.Font("helv")
except Exception:
    FONT_FILE = None
    TEXT_FONT = pymupdf.Font("helv")


def data_dir(*sub):
    d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    d = os.path.join(d, *sub)
    os.makedirs(d, exist_ok=True)
    return d


def documents_dir():
    return QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser("~")


_icon_cache = {}


def emoji_icon(ch, size=64):
    """Icône dessinée à partir d'un emoji (couleur sous Windows)."""
    key = (ch, size)
    if key not in _icon_cache:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(int(size * 0.78))
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, ch)
        p.end()
        _icon_cache[key] = QIcon(pm)
    return _icon_cache[key]


def pix_to_qimage(pix):
    if pix.n == 1:
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_Grayscale8).copy()
    fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


def qimage_to_bytes(img, fmt="PNG", quality=-1):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, fmt, quality)
    buf.close()
    return bytes(ba)


def load_image_file(path, max_side=2400):
    reader = QImageReader(path)
    reader.setAutoTransform(True)          # respecte l'orientation des photos de téléphone
    img = reader.read()
    if img.isNull():
        return None
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img


def image_bytes_for_pdf(img):
    if img.hasAlphaChannel():
        return qimage_to_bytes(img, "PNG")
    return qimage_to_bytes(img.convertToFormat(QImage.Format_RGB888), "JPEG", 88)


def add_image_as_page(doc, img, index=-1):
    """Ajoute une image comme nouvelle page A4 (orientée selon l'image)."""
    pw, ph = (842, 595) if img.width() > img.height() else (595, 842)
    page = doc.new_page(pno=index, width=pw, height=ph)
    m = 18
    page.insert_image(pymupdf.Rect(m, m, pw - m, ph - m), stream=image_bytes_for_pdf(img),
                      keep_proportion=True)


def make_white_transparent(img, threshold=225, dark=120):
    """Rend le fond blanc d'une signature scannée/photographiée transparent."""
    if max(img.width(), img.height()) > 1200:
        img = img.scaled(1200, 1200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    data = bytearray(img.constBits().tobytes())
    span = threshold - dark
    for i in range(0, len(data), 4):
        lum = (data[i] + data[i + 1] + data[i + 2]) // 3
        if lum >= threshold:
            data[i + 3] = 0
        elif lum > dark:
            data[i + 3] = int(255 * (threshold - lum) / span)
    return QImage(bytes(data), w, h, bpl, QImage.Format_ARGB32).copy()


def text_line_height():
    return TEXT_FONT.ascender - TEXT_FONT.descender


def text_block_size(text, fs):
    lines = text.split("\n") or [""]
    width = max(TEXT_FONT.text_length(l, fontsize=fs) for l in lines)
    return width + fs * 0.6, len(lines) * text_line_height() * fs + fs * 0.4


# --- Objets modifiables (textes et coches = annotations PDF) -----------------
def get_meta(doc, annot):
    try:
        t, v = doc.xref_get_key(annot.xref, META_KEY)
        return json.loads(v) if t == "string" else None
    except Exception:
        return None


def set_meta(doc, annot, meta):
    doc.xref_set_key(annot.xref, META_KEY, pymupdf.get_pdf_str(json.dumps(meta)))


def find_annot(page, xref):
    for a in page.annots() or []:
        if a.xref == xref:
            return a
    return None


def text_html(meta):
    lines = []
    for line in meta["t"].split("\n"):
        e = html.escape(line).replace("  ", " &#160;")
        lines.append(e or "&#160;")
    body = "<br>".join(lines)
    if meta.get("u"):
        body = f"<u>{body}</u>"
    if meta.get("i"):
        body = f"<i>{body}</i>"
    if meta.get("b"):
        body = f"<b>{body}</b>"
    return f'<p style="font-family:sans-serif;font-size:{meta["fs"]}pt;color:{meta["c"]}">{body}</p>'


def text_meta_size(meta):
    fs = meta["fs"]
    lines = meta["t"].split("\n")
    w = max(TEXT_FONT.text_length(l, fontsize=fs) for l in lines)
    w = w * (1.15 if meta.get("b") else 1.07) + fs * 0.8 + 4
    return w, len(lines) * TEXT_LH * fs + fs * 0.4


def create_text_annot(doc, page, x, y, meta):
    """Texte modifiable : (x, y) = coin haut-gauche dans la page telle qu'affichée."""
    w, h = text_meta_size(meta)
    r = pymupdf.Rect(x, y, x + w, y + h) * page.derotation_matrix
    a = page.add_freetext_annot(r, text_html(meta), richtext=True, rotate=page.rotation,
                                border_width=0, style="white-space:normal")
    a.set_flags(pymupdf.PDF_ANNOT_IS_PRINT)
    a.update()
    # NB : ne pas utiliser set_info(content=…) : MuPDF supprimerait le texte enrichi (/RC)
    doc.xref_set_key(a.xref, "T", pymupdf.get_pdf_str(APP_NAME))
    doc.xref_set_key(a.xref, "CL", "null")          # pas de ligne de rappel
    set_meta(doc, a, meta)
    return a


def create_check_annot(doc, page, cx, cy, size, mark):
    """Coche modifiable, centrée sur (cx, cy) dans la page telle qu'affichée."""
    x0, y0 = cx - size / 2, cy - size / 2
    if mark == "x":
        strokes = [[(0.2, 0.2), (0.8, 0.8)], [(0.8, 0.2), (0.2, 0.8)]]
    else:
        strokes = [[(0.14, 0.55), (0.42, 0.83), (0.9, 0.15)]]
    pts = [[tuple(pymupdf.Point(x0 + size * fx, y0 + size * fy) * page.derotation_matrix) for fx, fy in st]
           for st in strokes]
    a = page.add_ink_annot(pts)
    a.set_border(width=max(1.0, min(3.0, size * 0.13)))
    a.set_colors(stroke=(0.05, 0.08, 0.22))
    a.set_info(content="Case cochée", title=APP_NAME)
    a.set_flags(pymupdf.PDF_ANNOT_IS_PRINT)
    a.update()
    set_meta(doc, a, {"k": "check", "s": size, "m": mark})
    return a


def find_boxes(page):
    """Repère les petites cases carrées dessinées sur la page (formulaires « à imprimer »)."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out
    rm = page.rotation_matrix
    for d in drawings:
        r = d["rect"]
        if 5 <= r.width <= 28 and 5 <= r.height <= 28 and 0.75 <= r.width / r.height <= 1.33:
            v = r * rm
            if not any(abs(v.x0 - o.x0) < 2 and abs(v.y0 - o.y0) < 2 for o in out):
                out.append(v)
    return out


# --- Détection des cases sur un formulaire « à imprimer » ---------------------
COMB_RE = re.compile(r"^[Il|\[]?(?:_+[Il|\]])+$")
LINE_RE = re.compile(r"^_{5,}[.,;:]?$")
BOX_GLYPHS = set("□☐❑❒▢\uf06f\uf0a8\uf071\uf072")


def _is_blank(t):
    return bool(COMB_RE.match(t) and t.count("_") >= 2) or bool(LINE_RE.match(t))


def ink_bbox(page, r):
    """Contour réel (pixels foncés) d'un petit symbole, ex. la case « □ » d'une police."""
    clip = pymupdf.Rect(r) + (-1, -1, 1, 1)
    z = 6
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False, annots=False)
    s, n, W, H = pix.samples, pix.n, pix.width, pix.height
    xs, ys = [], []
    for y in range(H):
        row = y * pix.stride
        for x in range(W):
            i = row + x * n
            if s[i] + s[i + 1] + s[i + 2] < 450:
                xs.append(x)
                ys.append(y)
    if not xs:
        return pymupdf.Rect(r)
    return pymupdf.Rect(clip.x0 + min(xs) / z, clip.y0 + min(ys) / z,
                        clip.x0 + (max(xs) + 1) / z, clip.y0 + (max(ys) + 1) / z)


def _vband(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def _clean_label(t):
    t = "".join(ch for ch in t if ch not in BOX_GLYPHS)
    t = re.sub(r"\s+", " ", t).strip(" :\u00a0-–")
    return t[:1].upper() + t[1:] if t else t


def _left_label(words, x, y0, y1):
    """Texte juste à gauche de x, sur la même ligne (s'arrête sur un grand espace)."""
    cand = sorted((w for w in words if _vband(w[1], w[3], y0, y1) > 2 and w[2] <= x + 2), key=lambda w: w[0])
    run, right = [], x
    for w in reversed(cand):
        if _is_blank(w[4]) or right - w[2] > 12:
            break
        run.insert(0, w[4])
        right = w[0]
    return _clean_label(" ".join(run))


def _right_label(words, x, y0, y1, limit=70):
    cand = sorted((w for w in words if _vband(w[1], w[3], y0, y1) > 2 and w[0] >= x - 1), key=lambda w: w[0])
    run, left = [], x
    for w in cand:
        if w[0] - left > (22 if not run else 10) or _is_blank(w[4]):
            break
        run.append(w[4])
        left = w[2]
        if len(" ".join(run)) > limit:
            run.append("…")
            break
    return _clean_label(" ".join(run))


def _sections(page, boxes):
    """Titres en gras (ex. « Identité du second partenaire », « Article 3- Régime des biens »)."""
    out = []
    try:
        for b in page.get_text("dict")["blocks"]:
            prev = None                       # pour réunir un titre écrit sur deux lignes
            for l in b.get("lines", []):
                spans = [sp for sp in l["spans"] if sp["text"].strip()]
                bold = spans and all(sp["flags"] & 16 or "bold" in sp["font"].lower() for sp in spans)
                if not bold:
                    prev = None
                    continue
                raw = " ".join(sp["text"].strip() for sp in spans)
                bb = pymupdf.Rect(l["bbox"])
                if prev and bb.y0 - prev[1].y1 < 6:
                    raw = prev[0] + " " + raw
                    bb = prev[1] | bb
                    out = [o for o in out if o[2] is not prev[1]]
                if any(_vband(bb.y0 - 3, bb.y1 + 3, x.y0, x.y1) > 0 and x.x0 < bb.x1 for x in boxes):
                    prev = None               # c'est le libellé d'une case à cocher
                    continue
                txt = raw.rstrip()
                merged = prev is not None and raw.startswith(prev[0])
                if txt.endswith(":") or (len(txt) <= (170 if merged else 70) and not txt.endswith((".", ",", ";"))):
                    t = _clean_label(raw)
                    if 3 < len(t) < 140:
                        out.append((bb.y0, t, bb))
                prev = (raw, bb)
    except Exception:
        pass
    return sorted((y, t) for y, t, _ in out)


def detect_fields(page, ctx):
    """Renvoie les cases probables d'une page (coordonnées non tournées).
    ctx = {"section": …, "prev": …} est transmis d'une page à la suivante."""
    words = page.get_text("words")
    plain = [w for w in words if not _is_blank(w[4])]
    cands = []

    def text_above(x0, x1, top):
        """Libellé placé sur la ligne du dessus (ex. « … à la mairie de : » puis une ligne vide)."""
        above = [w for w in plain if top - 16 < w[3] <= top + 2 and w[2] > x0 - 2 and w[0] < x1]
        if not above:
            return ""
        y = max(w[3] for w in above)
        line = sorted((w for w in above if abs(w[3] - y) < 3), key=lambda w: w[0])
        return _clean_label(" ".join(w[4] for w in line[-8:]))

    def add_line(x0, x1, y, source):
        top = y - 14
        prev = [c for c in cands if c["kind"] == "text" and 0 < y - c["rect"].y1 < 34]
        if prev:
            top = max(top, max(c["rect"].y1 for c in prev) + 1)
        r = pymupdf.Rect(x0 + 1, top, x1, y - 0.5)
        if r.height < 6 or any(c["rect"].intersects(r) for c in cands):
            return
        if source == "draw":      # du texte posé sur la ligne = texte souligné ou tableau, pas une case
            over = sum(min(w[2], x1) - max(w[0], x0) for w in plain
                       if _vband(w[1], w[3], top, y) > 3 and min(w[2], x1) - max(w[0], x0) > 0)
            if over > 0.3 * (x1 - x0):
                return
        label = _left_label(words, x0, top, y + 1)
        if not label:
            near = [c for c in cands if c["kind"] == "text" and 0 < y - c["rect"].y1 < 34
                    and abs(c["rect"].x1 - x1) < 40]
            if near:
                label = near[-1]["label"].split(" (suite)")[0] + " (suite)"
            elif source == "text":    # un trait dessiné sans libellé est souvent une bordure de tableau
                label = text_above(x0, x1, top)
        if not label:
            return
        cands.append({"kind": "text", "rect": r, "label": label})

    # 1) séquences I__I__I (dates, codes postaux, téléphones)
    for w in words:
        if COMB_RE.match(w[4]):
            n = len(re.findall(r"_+", w[4]))
            if n >= 2:
                r = pymupdf.Rect(w[0], w[1] - 1, w[2], w[3] + 1)
                cands.append({"kind": "comb", "rect": r, "n": n,
                              "label": _left_label(words, w[0], w[1], w[3]) or text_above(w[0], w[2], w[1])})
    # 2) lignes : tirets bas tapés au clavier (« ______ ») ou traits dessinés
    lines = []
    for w in words:
        if LINE_RE.match(w[4]):
            lines.append((w[0], w[2], w[3] - 1.5, "text"))
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "re" and it[1].height < 2.5 and it[1].width > 25:
                lines.append((it[1].x0, it[1].x1, it[1].y0, "draw"))
            elif it[0] == "l" and abs(it[1].y - it[2].y) < 1 and abs(it[2].x - it[1].x) > 25:
                lines.append((min(it[1].x, it[2].x), max(it[1].x, it[2].x), it[1].y, "draw"))
    lines.sort(key=lambda l: (round(l[2]), l[0]))
    for x0, x1, y, src in lines:
        add_line(x0, x1, y, src)
    # 3) cases à cocher
    blocks = page.get_text("blocks")
    boxes = find_boxes(page) if page.rotation == 0 else []
    if page.rotation == 0:
        try:
            for b in page.get_text("rawdict")["blocks"]:
                for l in b.get("lines", []):
                    for sp in l["spans"]:
                        for ch in sp["chars"]:
                            if ch["c"] in BOX_GLYPHS:
                                r = ink_bbox(page, ch["bbox"])
                                if 3 <= r.width <= 20 and not any(r.intersects(o) for o in boxes):
                                    # petites cases « □ » : zone agrandie pour une coche lisible et facile à cliquer
                                    c, half = (r.tl + r.br) / 2, max(r.width, r.height, 10) / 2
                                    boxes.append(pymupdf.Rect(c.x - half, c.y - half, c.x + half, c.y + half))
        except Exception:
            pass
    for b in boxes:
        if any(c["rect"].intersects(b) for c in cands):
            continue
        label = _right_label(words, b.x1, b.y0, b.y1)
        # « Oui ☐  Non ☐ » : le libellé est à gauche de chaque case
        right = [w for w in words if _vband(w[1], w[3], b.y0, b.y1) > 2 and 0 <= w[0] - b.x1 < 12]
        left = [w for w in words if _vband(w[1], w[3], b.y0, b.y1) > 2 and 0 <= b.x0 - w[2] < 10]
        right_is_next = right and any(0 <= o.x0 - right[0][2] < 12 for o in boxes if o is not b)
        if left and (not right or right_is_next) and len(left[0][4]) <= 12:
            label = _clean_label(left[0][4])
        if len(label) < 5 or label.lower() in ("oui", "non"):
            q = ""
            for bl in blocks:
                if bl[0] < b.x0 and _vband(bl[1], bl[3], b.y0, b.y1) > 2 and "?" in bl[4]:
                    q = _clean_label(bl[4].split("?")[0]) + " ?"
            if q:
                q = q if len(q) <= 70 else q[:67] + "… ?"
                label = f"{q} — {label}" if label else q
        cands.append({"kind": "check", "rect": pymupdf.Rect(b), "label": label or "Case à cocher"})
    # libellés trop courts (« à ») : on ajoute le contexte du champ précédent
    cands.sort(key=lambda c: (round(c["rect"].y1 / 4), c["rect"].x0))
    prev = ctx.get("prev", "")
    for c in cands:
        if len(c["label"]) <= 3 and prev:
            base = prev.split(" (suite)")[0]
            short = {"à": "lieu", "le": "date", "i": "suite", "": "suite"}.get(c["label"].lower(), c["label"])
            c["label"] = f"{base} — {short}"
        elif c["kind"] != "check":
            prev = c["label"]
    ctx["prev"] = prev
    # sections
    secs = _sections(page, boxes)
    for c in cands:
        current = ctx.get("section", "")
        for y, t in secs:
            if y < c["rect"].y0:
                current = t
        c["section"] = current
    if secs:
        ctx["section"] = secs[-1][1]
    return cands


def new_candidates(page, cands):
    """Garde seulement les cases qui ne recouvrent pas un champ déjà présent."""
    existing = [w.rect for w in page.widgets() or []]
    return [c for c in cands if not any((c["rect"] & r).get_area() > 0.25 * c["rect"].get_area() for r in existing)]


def add_detected_fields(doc, page, cands, used_names=None):
    used = used_names if used_names is not None else set()
    k = 0
    for c in cands:
        while True:
            k += 1
            name = f"pf_p{page.number + 1}_{k}"
            if name not in used:
                break
        used.add(name)
        w = pymupdf.Widget()
        w.rect = c["rect"]
        w.field_name = name
        w.field_label = c["label"]
        w.border_width = 0
        w.border_color = None
        w.fill_color = None
        if c["kind"] == "check":
            w.field_type = W_CHECK
        else:
            w.field_type = W_TEXT
            w.text_font = "Helv"
            w.text_fontsize = 10 if c["kind"] == "text" else max(6.0, min(10.0, c["rect"].height * 0.8))
            if c["kind"] == "comb":
                w.field_flags |= pymupdf.PDF_TX_FIELD_IS_COMB
                w.text_maxlen = c["n"]
        annot = page.add_widget(w)
        try:
            doc.xref_set_key(annot.xref, "PFSection", pymupdf.get_pdf_str(c.get("section", "")))
        except Exception:
            pass


def comb_placeholder(label, n):
    low = (label or "").lower()
    if n == 8 and ("date" in low or low.endswith("le")):
        return "JJMMAAAA — ex. 12031960"
    if n == 5:
        return "5 chiffres"
    return f"{n} caractères maximum"


def widget_sort_key(page, w):
    r = w.rect * page.rotation_matrix
    return (round(r.y0 / 6), r.x0)


def parse_page_ranges(text, count):
    """« 1-3, 5 » → [0, 1, 2, 4]. Renvoie None si la saisie n'est pas comprise."""
    pages = []
    for part in re.split(r"[,;]", text.replace(" ", "")):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a > b:
            a, b = b, a
        if a < 1 or b > count:
            return None
        pages.extend(range(a - 1, b))
    return pages or None


# --- Compression -------------------------------------------------------------
# (seuil dpi, dpi cible, qualité JPEG) — du plus doux au plus fort
COMPRESS_LEVELS = [None, (230, 200, 85), (180, 150, 75), (140, 120, 65), (110, 96, 55), (85, 72, 45), (65, 55, 35)]


def human_size(n):
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} Ko"
    return f"{n / (1024 * 1024):.1f} Mo".replace(".", ",")


def compress_pdf(data, level, gray=False):
    """Renvoie les octets d'une copie allégée. level 0 = sans perte (nettoyage seulement)."""
    d = pymupdf.open("pdf", data)
    try:
        if level > 0:
            t, target, q = COMPRESS_LEVELS[level]
            d.rewrite_images(dpi_threshold=t, dpi_target=target, quality=q, set_to_gray=gray)
        elif gray:
            d.rewrite_images(dpi_threshold=100000, dpi_target=0, quality=90, set_to_gray=True)
        try:
            d.subset_fonts()
        except Exception:
            pass
        return d.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
    finally:
        d.close()


def pretty_field_name(name):
    if not name:
        return "Champ"
    last = name.split(".")[-1]
    while "[" in last and last.endswith("]"):
        last = last[:last.rfind("[")]
    return (last or name).replace("_", " ").strip()


# --- Boîtes de dialogue en français -----------------------------------------
def _box(parent, text, title, icon, buttons, default=0, detail=None):
    box = QMessageBox(parent)
    box.setWindowTitle(title or APP_NAME)
    box.setIcon(icon)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    btns = [box.addButton(t, role) for t, role in buttons]
    box.setDefaultButton(btns[default])
    box.setEscapeButton(btns[-1])
    box.exec()
    clicked = box.clickedButton()
    return btns.index(clicked) if clicked in btns else len(btns) - 1


def info(parent, text, title=None):
    _box(parent, text, title, QMessageBox.Information, [("OK", QMessageBox.AcceptRole)])


def warn(parent, text, title=None, detail=None):
    _box(parent, text, title, QMessageBox.Warning, [("OK", QMessageBox.AcceptRole)], detail=detail)


def ask(parent, text, buttons, title=None, default=0):
    return _box(parent, text, title, QMessageBox.Question, buttons, default)


# ----------------------------------------------------------------------------
# Contenu pédagogique
# ----------------------------------------------------------------------------
TOUR_STEPS = [
    ("👋", "Bienvenue dans PDF Facile",
     "Ce logiciel vous permet de lire, remplir, signer et modifier vos documents PDF.\n\n"
     "Ce petit guide vous montre l'essentiel en une minute. Vous pouvez le passer à tout moment."),
    ("📂", "Ouvrir un document",
     "Cliquez sur le bouton « Ouvrir » en haut à gauche, puis choisissez votre fichier.\n\n"
     "Vous pouvez aussi faire glisser un fichier PDF directement dans la fenêtre."),
    ("📄", "Les pages",
     "À gauche, vous voyez toutes les pages en miniature. Cliquez sur une page pour l'afficher.\n\n"
     "Les boutons en dessous permettent de la tourner, de la déplacer, de la supprimer ou d'en ajouter."),
    ("📝", "Remplir un formulaire",
     "Si le document contient des cases à remplir, elles apparaissent en bleu : cliquez dedans et tapez. "
     "La touche Tab passe à la case suivante.\n\nSinon, utilisez « Écrire du texte » et « Cocher une case », "
     "puis cliquez directement sur la page."),
    ("✍️", "Signer un document",
     "Cliquez sur « Signer », dessinez votre signature avec la souris, puis cliquez sur le "
     "document à l'endroit où vous voulez la placer.\n\nVotre signature est gardée pour la prochaine fois."),
    ("↩️", "Pas de panique !",
     "Vous avez fait une erreur ? Cliquez sur « Annuler » : la dernière action est effacée.\n\n"
     "Rien n'est modifié sur votre ordinateur tant que vous n'avez pas cliqué sur « Enregistrer »."),
    ("💾", "Enregistrer",
     "Quand vous avez terminé, cliquez sur « Enregistrer ».\n\n"
     "Pour garder l'original intact, cliquez sur la petite flèche à côté et choisissez "
     "« Enregistrer sous… » pour créer une copie."),
]

TIPS = [
    "Cliquez sur une miniature à gauche pour aller directement à cette page.",
    "Vous pouvez changer l'ordre des pages en faisant glisser les miniatures à gauche.",
    "Une erreur ? Le bouton « Annuler » (ou Ctrl+Z) efface la dernière action.",
    "Pour tourner une page à l'envers, cliquez deux fois sur « Tourner à droite ».",
    "Maintenez la touche Ctrl et cliquez sur plusieurs miniatures pour tourner ou supprimer plusieurs pages à la fois.",
    "Utilisez « Enregistrer sous… » pour garder une copie du document original.",
    "Pendant que vous placez une signature ou une image, la molette de la souris permet de l'agrandir ou la réduire.",
    "Maintenez Ctrl et tournez la molette de la souris pour zoomer.",
    "« Écrire du texte » permet de compléter un document qui n'a pas de cases à remplir.",
    "Le bouton « Combiner » réunit plusieurs PDF ou photos en un seul document.",
    "Cliquez sur un texte que vous avez écrit pour le corriger. Faites-le glisser pour le déplacer.",
    "Clic droit sur un texte ou une coche que vous avez ajouté pour le supprimer.",
    "Dans « Écrire du texte », le bouton « Date du jour » ajoute la date automatiquement.",
]

STYLE = """
QWidget { font-family: "Segoe UI", "Arial"; font-size: 11pt; color: #1f2937; }
QMainWindow, #workspace, #welcome { background: #f3f4f6; }
#topbar { background: #ffffff; border-bottom: 1px solid #d1d5db; }
QToolButton#big { border: 1px solid transparent; border-radius: 8px; padding: 4px 2px;
    font-size: 10pt; min-width: 66px; background: transparent; }
QToolButton#big:hover { background: #e0ecff; border-color: #93c5fd; }
QToolButton#big:pressed, QToolButton#big:checked { background: #bfdbfe; border-color: #3b82f6; }
QToolButton#big:disabled { color: #9ca3af; }
QToolButton#big::menu-button { width: 16px; border-left: 1px solid #e5e7eb; }
QFrame#sep { background: #e5e7eb; max-width: 1px; min-width: 1px; margin: 8px 4px; }
QPushButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 12px; }
QPushButton:hover { background: #eff6ff; border-color: #60a5fa; }
QPushButton:pressed { background: #dbeafe; }
QPushButton:disabled { color: #9ca3af; background: #f9fafb; }
QPushButton#primary { background: #2563eb; color: white; border-color: #1d4ed8; font-weight: 600; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:disabled { background: #93c5fd; }
QPushButton#huge { font-size: 14pt; padding: 16px 26px; border-radius: 12px; text-align: left; }
#pagebtn QPushButton { padding: 7px 4px; font-size: 10pt; }
QPushButton#link { border: none; background: transparent; color: #1d4ed8; text-decoration: underline; padding: 2px 6px; }
#tipbar { background: #fff8db; border-bottom: 1px solid #f3d56b; }
#modebar { background: #dbeafe; border-bottom: 1px solid #93c5fd; }
#modebar[kind="success"] { background: #dcfce7; border-bottom: 1px solid #86efac; }
#modebar[kind="warning"] { background: #fee2e2; border-bottom: 1px solid #fca5a5; }
#sidepanel { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; }
#paneltitle { font-size: 13pt; font-weight: 700; }
#hint { color: #4b5563; font-size: 10pt; }
QListWidget { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; }
QListWidget::item { padding: 4px; border-radius: 6px; }
QListWidget::item:selected { background: #bfdbfe; color: #1e3a8a; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: white; border: 1px solid #cbd5e1;
    border-radius: 6px; padding: 5px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 2px solid #3b82f6; }
QGroupBox { border: 1px solid #e5e7eb; border-radius: 6px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
#navbar { background: #ffffff; border-top: 1px solid #d1d5db; }
QScrollArea { border: none; }
"""


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
def big_button(emoji, text, tip, slot=None):
    b = QToolButton()
    b.setObjectName("big")
    b.setIcon(emoji_icon(emoji))
    b.setIconSize(QSize(30, 30))
    b.setText(text)
    b.setToolTip(tip)
    b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def separator():
    s = QFrame()
    s.setObjectName("sep")
    s.setFrameShape(QFrame.VLine)
    return s


class TipBar(QFrame):
    """Bandeau d'astuces, facile à fermer ou à désactiver."""
    disabled = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("tipbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        nxt = QPushButton("Astuce suivante")
        nxt.clicked.connect(self.next_tip)
        never = QPushButton("Ne plus afficher les astuces")
        never.setObjectName("link")
        never.setCursor(Qt.PointingHandCursor)
        never.clicked.connect(self._disable)
        close = QPushButton("✕")
        close.setToolTip("Fermer l'astuce")
        close.setFixedWidth(40)
        close.clicked.connect(self.hide)
        for w in (nxt, never, close):
            lay.addWidget(w)
        self.index = 0

    def show_tip(self, index):
        self.index = index % len(TIPS)
        self.label.setText(f"💡 <b>Astuce :</b> {TIPS[self.index]}")
        self.show()

    def next_tip(self):
        self.show_tip(self.index + 1)

    def _disable(self):
        self.hide()
        self.disabled.emit()


class ModeBar(QFrame):
    """Bandeau d'instructions (mode placement) ou de confirmation."""
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("modebar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        f = self.label.font()
        f.setPointSize(12)
        self.label.setFont(f)
        lay.addWidget(self.label, 1)
        self._extra = None
        self._lay = lay
        self.cancel_btn = QPushButton("Terminer (Échap)")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        lay.addWidget(self.cancel_btn)
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedWidth(40)
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.close_btn)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, html, kind="mode", cancel=True, timeout=0, extra=None):
        if self._extra is not None and self._extra is not extra:
            self._lay.removeWidget(self._extra)
            self._extra.hide()
            self._extra = None
        if extra is not None and self._extra is None:
            self._lay.insertWidget(1, extra)
            extra.show()
            self._extra = extra
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(html)
        self.cancel_btn.setVisible(cancel)
        self.close_btn.setVisible(not cancel)
        self.timer.stop()
        if timeout:
            self.timer.start(timeout)
        self.show()


class ThumbList(QListWidget):
    orderChanged = Signal()
    deleteRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.ListMode)
        self.setIconSize(QSize(110, 150))
        self.setSpacing(3)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setUniformItemSizes(True)

    def dropEvent(self, e):
        super().dropEvent(e)
        QTimer.singleShot(0, self.orderChanged.emit)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(e)


class PageScroll(QScrollArea):
    """Zone de défilement : continuer à tourner la molette change de page."""
    nextPage = Signal()
    prevPage = Signal()
    resized = Signal()

    def __init__(self):
        super().__init__()
        self._acc = 0
        self.setAlignment(Qt.AlignCenter)
        self.setWidgetResizable(False)

    def wheelEvent(self, e):
        sb = self.verticalScrollBar()
        dy = e.angleDelta().y()
        if dy < 0 and sb.value() >= sb.maximum():
            self._acc += -dy
            if self._acc >= 240:
                self._acc = 0
                self.nextPage.emit()
            e.accept()
        elif dy > 0 and sb.value() <= sb.minimum():
            self._acc += dy
            if self._acc >= 240:
                self._acc = 0
                self.prevPage.emit()
            e.accept()
        else:
            self._acc = 0
            super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class PageView(QWidget):
    """Affiche une page, les cases de formulaire, et gère les modes de placement."""
    placed = Signal(QRectF)
    imageClicked = Signal(int)
    fieldClicked = Signal(int)
    zoomWheel = Signal(int)
    typeAt = Signal(QPointF)
    checkAt = Signal(QPointF, QRectF)
    objectClicked = Signal(int)
    objectMoved = Signal(int, float, float)
    objectMenu = Signal(int, QPoint)
    fieldMenu = Signal(int, QPoint)
    backgroundClicked = Signal()

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.img = None
        self.scale = 1.0
        self.pw = self.ph = 0
        self.fields = []
        self.images = []
        self.mode = None
        self.ghost_img = None
        self.ghost_w = 150.0
        self.ghost_aspect = 1.0
        self.ghost_text = ""
        self.text_size = 12
        self.text_color = QColor("black")
        self.mouse = None
        self.hover_img = -1
        self.hover_field = -1
        self.objects = []
        self.object_kinds = []
        self.boxes = []
        self.hover_obj = -1
        self.hover_box = -1
        self._press_obj = -1
        self._press_pos = None
        self._drag = None

    def set_content(self, img, scale, pw, ph, fields, images, objects=(), boxes=(), kinds=()):
        self.img, self.scale, self.pw, self.ph = img, scale, pw, ph
        self.fields, self.images = fields, images
        self.objects, self.boxes, self.object_kinds = list(objects), list(boxes), list(kinds)
        self.hover_img = self.hover_field = self.hover_obj = self.hover_box = -1
        self.setFixedSize(int(pw * scale) + 2 * MARGIN, int(ph * scale) + 2 * MARGIN)
        self.update()

    def to_pdf(self, pos):
        return QPointF((pos.x() - MARGIN) / self.scale, (pos.y() - MARGIN) / self.scale)

    def to_widget(self, r):
        s = self.scale
        return QRectF(MARGIN + r.x() * s, MARGIN + r.y() * s, r.width() * s, r.height() * s)

    def ghost_rect(self):
        if self.mouse is None:
            return None
        p = self.to_pdf(self.mouse)
        if self.mode == "place_image":
            w = self.ghost_w
            h = w / self.ghost_aspect
            r = QRectF(p.x() - w / 2, p.y() - h / 2, w, h)
        elif self.mode == "place_text":
            w, h = text_block_size(self.ghost_text, self.text_size)
            r = QRectF(p.x(), p.y() - text_line_height() * self.text_size / 2 - self.text_size * 0.2, w, h)
        else:
            return None
        # garder dans la page
        if r.right() > self.pw:
            r.moveRight(self.pw)
        if r.bottom() > self.ph:
            r.moveBottom(self.ph)
        if r.left() < 0:
            r.moveLeft(0)
        if r.top() < 0:
            r.moveTop(0)
        return r

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#e5e7eb"))
        if self.img is None:
            return
        p.setRenderHint(QPainter.Antialiasing)
        page_r = QRectF(MARGIN, MARGIN, self.pw * self.scale, self.ph * self.scale)
        p.fillRect(page_r.translated(3, 4), QColor(0, 0, 0, 45))
        p.drawImage(page_r, self.img)

        if self.mode is None:
            for i, (r, _x) in enumerate(self.fields):
                wr = self.to_widget(r)
                p.fillRect(wr, QColor(59, 130, 246, 80 if i == self.hover_field else 38))
                p.setPen(QPen(QColor(37, 99, 235, 170), 1))
                p.drawRect(wr)

        if self.mode == "remove_image":
            for i, r in enumerate(self.images):
                wr = self.to_widget(r)
                if i == self.hover_img:
                    p.fillRect(wr, QColor(220, 38, 38, 90))
                    p.setPen(QPen(QColor("#dc2626"), 3))
                    p.drawRect(wr)
                    f = p.font()
                    f.setPointSize(13)
                    f.setBold(True)
                    p.setFont(f)
                    p.setPen(QColor("white"))
                    p.drawText(wr, Qt.AlignCenter, "✖ Cliquer pour retirer")
                else:
                    p.setPen(QPen(QColor(220, 38, 38, 150), 2, Qt.DashLine))
                    p.drawRect(wr)

        if self.mode == "check":
            for i, r in enumerate(self.boxes):
                wr = self.to_widget(r).adjusted(-2, -2, 2, 2)
                p.fillRect(wr, QColor(22, 163, 74, 90 if i == self.hover_box else 35))
                p.setPen(QPen(QColor(22, 163, 74, 200), 2 if i == self.hover_box else 1))
                p.drawRect(wr)

        if self.mode in (None, "type", "check"):
            for i, r in enumerate(self.objects):
                if i == self.hover_obj or (self._drag and i == self._press_obj):
                    wr = self.to_widget(r).adjusted(-3, -3, 3, 3)
                    p.setPen(QPen(QColor("#2563eb"), 1.5, Qt.DashLine))
                    p.setBrush(QColor(37, 99, 235, 18))
                    p.drawRect(wr)
                    p.setBrush(Qt.NoBrush)
                    if i == self.hover_obj and not self._drag and i < len(self.object_kinds):
                        tip = ("✏️ Cliquer pour modifier · glisser pour déplacer" if self.object_kinds[i] == "text"
                               else ("Cliquer pour enlever" if self.mode == "check" else "Glisser pour déplacer"))
                        f = QFont(self.font())
                        f.setPointSize(9)
                        p.setFont(f)
                        tw = p.fontMetrics().horizontalAdvance(tip) + 14
                        badge = QRectF(wr.left(), wr.top() - 22, tw, 20)
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor("#1d4ed8"))
                        p.drawRoundedRect(badge, 6, 6)
                        p.setPen(QColor("white"))
                        p.drawText(badge, Qt.AlignCenter, tip)
                        p.setBrush(Qt.NoBrush)
            if self._drag and self._press_obj >= 0:
                r = self.objects[self._press_obj].translated(*self._drag)
                p.setPen(QPen(QColor("#2563eb"), 2))
                p.setBrush(QColor(37, 99, 235, 40))
                p.drawRect(self.to_widget(r))
                p.setBrush(Qt.NoBrush)

        gr = self.ghost_rect()
        if gr is not None:
            wr = self.to_widget(gr)
            if self.mode == "place_image" and self.ghost_img is not None:
                p.setOpacity(0.8)
                p.drawImage(wr, self.ghost_img)
                p.setOpacity(1)
            elif self.mode == "place_text":
                f = QFont("Arial")
                f.setPixelSize(max(1, int(self.text_size * self.scale)))
                p.setFont(f)
                p.setPen(self.text_color)
                lh = text_line_height() * self.text_size * self.scale
                base = wr.top() + self.text_size * self.scale * (0.2 + TEXT_FONT.ascender)
                for i, line in enumerate(self.ghost_text.split("\n")):
                    p.drawText(QPointF(wr.left() + 1, base + i * lh), line)
            p.setPen(QPen(QColor("#2563eb"), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(wr)

    def _hit(self, rects, pos):
        pt = self.to_pdf(pos)
        best, area = -1, None
        for i, r in enumerate(rects):
            if r.contains(pt):
                a = r.width() * r.height()
                if area is None or a < area:
                    best, area = i, a
        return best

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        if self._press_obj >= 0 and e.buttons() & Qt.LeftButton:
            d = e.position() - self._press_pos
            if self._drag or abs(d.x()) + abs(d.y()) > 5:
                self._drag = (d.x() / self.scale, d.y() / self.scale)
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
            return
        self.hover_obj = -1
        if self.mode in (None, "type", "check"):
            self.hover_obj = self._hit(self.objects, self.mouse)
            if self.hover_obj >= 0:
                self.hover_box = -1
                self.setCursor(Qt.OpenHandCursor if self.mode is None else Qt.PointingHandCursor)
                self.update()
                return
        if self.mode == "check":
            self.hover_box = self._hit(self.boxes, self.mouse)
            self.setCursor(Qt.PointingHandCursor)
        elif self.mode == "remove_image":
            self.hover_img = self._hit(self.images, self.mouse)
            self.setCursor(Qt.PointingHandCursor if self.hover_img >= 0 else Qt.ArrowCursor)
        elif self.mode in ("place_image", "place_text"):
            self.setCursor(Qt.CrossCursor)
        elif self.mode == "type":
            self.setCursor(Qt.IBeamCursor)
        else:
            self.hover_field = self._hit([r for r, _ in self.fields], self.mouse)
            self.setCursor(Qt.IBeamCursor if self.hover_field >= 0 else Qt.ArrowCursor)
        self.update()

    def leaveEvent(self, e):
        self.mouse = None
        self.hover_img = self.hover_field = self.hover_obj = self.hover_box = -1
        self.update()

    def mousePressEvent(self, e):
        if self.img is None:
            return
        self.mouse = e.position()
        obj = self._hit(self.objects, self.mouse) if self.mode in (None, "type", "check") else -1
        if e.button() == Qt.RightButton:
            if obj >= 0:
                self.objectMenu.emit(obj, e.globalPosition().toPoint())
            elif self.mode is None:
                f = self._hit([r for r, _ in self.fields], self.mouse)
                if f >= 0:
                    self.fieldMenu.emit(self.fields[f][1], e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if obj >= 0:
            self._press_obj, self._press_pos, self._drag = obj, e.position(), None
            return
        if self.mode == "check":
            pt = self.to_pdf(self.mouse)
            b = self._hit(self.boxes, self.mouse)
            if 0 <= pt.x() <= self.pw and 0 <= pt.y() <= self.ph:
                self.checkAt.emit(pt, self.boxes[b] if b >= 0 else QRectF())
        elif self.mode in ("place_image", "place_text"):
            gr = self.ghost_rect()
            if gr is not None:
                self.placed.emit(gr)
        elif self.mode == "remove_image":
            i = self._hit(self.images, self.mouse)
            if i >= 0:
                self.imageClicked.emit(i)
        elif self.mode == "type":
            pt = self.to_pdf(self.mouse)
            if 0 <= pt.x() <= self.pw and 0 <= pt.y() <= self.ph:
                self.typeAt.emit(pt)
        else:
            i = self._hit([r for r, _ in self.fields], self.mouse)
            if i >= 0:
                self.fieldClicked.emit(self.fields[i][1])
            else:
                self.backgroundClicked.emit()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press_obj < 0:
            return
        i, drag = self._press_obj, self._drag
        self._press_obj, self._press_pos, self._drag = -1, None, None
        self.update()
        if drag:
            self.objectMoved.emit(i, drag[0], drag[1])
        else:
            self.objectClicked.emit(i)

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if e.modifiers() & Qt.ControlModifier:
            self.zoomWheel.emit(dy)
            e.accept()
        elif self.mode == "place_image" and dy:
            f = 1.1 if dy > 0 else 1 / 1.1
            self.ghost_w = max(20.0, min(self.pw, self.ghost_w * f))
            self.update()
            e.accept()
        elif self.mode == "place_text" and dy:
            self.text_size = max(6, min(72, self.text_size + (1 if dy > 0 else -1)))
            self.update()
            e.accept()
        else:
            e.ignore()


class InlineEdit(QPlainTextEdit):
    """Zone de saisie posée directement sur la page. Entrée = valider, Maj+Entrée = nouvelle ligne."""
    commitRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self.document().setDocumentMargin(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.commitRequested.emit()
            return
        super().keyPressEvent(e)


class FieldLine(QLineEdit):
    """Saisie posée sur une case du formulaire. Entrée/Tab = case suivante, Maj+Tab = précédente."""
    navigate = Signal(int)

    def event(self, e):
        if e.type() == e.Type.KeyPress:
            if e.key() == Qt.Key_Tab:
                self.navigate.emit(1)
                return True
            if e.key() == Qt.Key_Backtab:
                self.navigate.emit(-1)
                return True
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.navigate.emit(1)
                return True
            if e.key() == Qt.Key_Escape:
                self.navigate.emit(0)
                return True
        return super().event(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            QTimer.singleShot(0, self, lambda: self.navigate.emit(0))

    def value(self):
        return self.text()


class FieldMulti(QPlainTextEdit):
    navigate = Signal(int)

    def event(self, e):
        if e.type() == e.Type.KeyPress:
            if e.key() == Qt.Key_Tab:
                self.navigate.emit(1)
                return True
            if e.key() == Qt.Key_Backtab:
                self.navigate.emit(-1)
                return True
            if e.key() == Qt.Key_Escape:
                self.navigate.emit(0)
                return True
        return super().event(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
            QTimer.singleShot(0, self, lambda: self.navigate.emit(0))

    def value(self):
        return self.toPlainText()


class FieldCombo(QComboBox):
    navigate = Signal(int)

    def value(self):
        return self.currentData()


class FormPanel(QFrame):
    """Liste des cases à remplir de la page affichée."""
    changed = Signal(int, int, object, str)   # page, xref, valeur, type
    writeRequested = Signal()
    detectRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("sidepanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        t = QLabel("📝 Formulaire")
        t.setObjectName("paneltitle")
        lay.addWidget(t)
        self.hint = QLabel("Vous pouvez remplir ici, ou directement en cliquant dans les cases bleues du document.")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)
        self.container = None
        self.editors = {}
        self.page_index = 0
        self._pending = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.flush)

    def flush(self):
        self._timer.stop()
        if self._pending:
            args, self._pending = self._pending, None
            self.changed.emit(*args)

    def _queue_text(self, xref, value):
        if self._pending and self._pending[1] != xref:
            self.flush()
        self._pending = (self.page_index, xref, value, "text")
        self._timer.start()

    def build(self, doc, page_index):
        self.flush()
        self.page_index = page_index
        self.editors = {}
        self.container = QWidget()
        v = QVBoxLayout(self.container)
        v.setContentsMargins(0, 4, 6, 4)
        v.setSpacing(10)
        page = doc[page_index]
        widgets = [w for w in (page.widgets() or []) if w.field_type != W_BUTTON]

        def pos(w):
            r = w.rect * page.rotation_matrix
            return (round(r.y0 / 6), r.x0)
        widgets.sort(key=pos)

        if not widgets and not any(p.first_widget is not None for p in doc):
            lab = QLabel("Ce document n'a <b>pas de cases à remplir</b> sur l'ordinateur "
                         "(c'est une version « à imprimer »).<br><br>"
                         "<b>PDF Facile peut essayer de trouver les cases tout seul :</b>")
            lab.setWordWrap(True)
            v.addWidget(lab)
            d = QPushButton("🔍 Trouver les cases à remplir")
            d.setObjectName("primary")
            d.clicked.connect(self.detectRequested.emit)
            v.addWidget(d)
            lab2 = QLabel("<br>Ou bien, écrivez directement sur la page :")
            lab2.setWordWrap(True)
            v.addWidget(lab2)
            b = QPushButton("🔤 Écrire sur le document")
            b.clicked.connect(self.writeRequested.emit)
            v.addWidget(b)
        elif not widgets:
            others = [i + 1 for i in range(doc.page_count)
                      if i != page_index and doc[i].first_widget is not None]
            msg = "Aucune case à remplir sur cette page."
            if others:
                pages = ", ".join(str(n) for n in others[:10])
                msg += f"\n\nIl y a des cases à remplir sur la page {pages}."
            lab = QLabel(msg)
            lab.setWordWrap(True)
            lab.setObjectName("hint")
            v.addWidget(lab)

        done_radio = set()
        section = None
        if any((w.field_name or "").startswith("pf_p") for w in widgets):
            note = QLabel("🔍 Cases trouvées automatiquement. S'il en manque une, utilisez « Écrire du texte ». "
                          "Clic droit sur une case bleue pour la retirer.")
            note.setWordWrap(True)
            note.setObjectName("hint")
            v.addWidget(note)
        for w in widgets:
            try:
                kind, val = doc.xref_get_key(w.xref, "PFSection")
                sec = val if kind == "string" else ""
            except Exception:
                sec = ""
            if sec and sec != section:
                section = sec
                h = QLabel(sec)
                h.setWordWrap(True)
                h.setStyleSheet("color:#1e3a8a; font-weight:700; font-size:12pt; "
                                "border-bottom:2px solid #bfdbfe; padding-top:8px;")
                v.addWidget(h)
            label = w.field_label or pretty_field_name(w.field_name)
            readonly = bool(w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY)
            xref = w.xref
            if w.field_type == W_TEXT:
                v.addWidget(self._label(label))
                if w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                    ed = QPlainTextEdit(w.field_value or "")
                    ed.setFixedHeight(90)
                    ed.textChanged.connect(lambda ed=ed, x=xref: self._queue_text(x, ed.toPlainText()))
                else:
                    ed = QLineEdit(w.field_value or "")
                    comb = bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB) and w.text_maxlen > 0
                    if comb:
                        n = w.text_maxlen
                        ed.setMaxLength(n + 4)       # on tolère « / » et espaces, retirés ensuite
                        ed.setPlaceholderText(comb_placeholder(label, n))
                        ed.textChanged.connect(lambda t, x=xref, n=n: self._queue_text(
                            x, re.sub(r"[\s/.\-]", "", t)[:n]))
                    else:
                        if w.text_maxlen and w.text_maxlen > 0:
                            ed.setMaxLength(w.text_maxlen)
                        ed.textChanged.connect(lambda t, x=xref: self._queue_text(x, t))
                    ed.editingFinished.connect(self.flush)
                ed.setReadOnly(readonly)
                v.addWidget(ed)
                self.editors[xref] = ed
            elif w.field_type == W_CHECK:
                cb = QCheckBox()
                cb.setChecked(w.field_value not in (False, None, "", "Off"))
                cb.setEnabled(not readonly)
                cb.toggled.connect(lambda c, x=xref: self.changed.emit(self.page_index, x, c, "check"))
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(cb, 0, Qt.AlignTop)
                lab = QLabel(html.escape(label))
                lab.setWordWrap(True)
                lab.setCursor(Qt.PointingHandCursor)
                lab.mousePressEvent = lambda e, c=cb: c.isEnabled() and c.toggle()
                row.addWidget(lab, 1)
                v.addLayout(row)
                self.editors[xref] = cb
            elif w.field_type == W_RADIO:
                if w.field_name in done_radio:
                    continue
                done_radio.add(w.field_name)
                group = [g for g in widgets if g.field_type == W_RADIO and g.field_name == w.field_name]
                box = QGroupBox(pretty_field_name(w.field_name) if not w.field_label else w.field_label)
                bl = QVBoxLayout(box)
                bg = QButtonGroup(box)
                for n, g in enumerate(group, 1):
                    st = g.on_state()
                    txt = st if isinstance(st, str) and st not in ("Yes", "On", "") else f"Choix {n}"
                    rb = QRadioButton(txt)
                    rb.setChecked(g.field_value not in (False, None, "", "Off"))
                    rb.setEnabled(not readonly)
                    rb.toggled.connect(lambda c, x=g.xref: c and self.changed.emit(self.page_index, x, True, "radio"))
                    bg.addButton(rb)
                    bl.addWidget(rb)
                    self.editors[g.xref] = rb
                v.addWidget(box)
            elif w.field_type in (W_COMBO, W_LIST):
                v.addWidget(self._label(label))
                cmb = QComboBox()
                values = []
                for c in (w.choice_values or []):
                    if isinstance(c, (list, tuple)):
                        values.append((str(c[0]), str(c[1] if len(c) > 1 else c[0])))
                    else:
                        values.append((str(c), str(c)))
                cmb.addItem("— choisir —", "")
                for export, shown in values:
                    cmb.addItem(shown, export)
                cur = w.field_value if isinstance(w.field_value, str) else ""
                idx = cmb.findData(cur)
                cmb.setCurrentIndex(max(0, idx))
                cmb.setEnabled(not readonly)
                cmb.currentIndexChanged.connect(
                    lambda i, c=cmb, x=xref: self.changed.emit(self.page_index, x, c.itemData(i), "choice"))
                v.addWidget(cmb)
                self.editors[xref] = cmb
            elif w.field_type == W_SIGN:
                lab = QLabel(f"✍️ <b>{label}</b><br>Zone de signature : cliquez sur « Signer » en haut, "
                             "puis cliquez sur cette zone du document.")
                lab.setWordWrap(True)
                v.addWidget(lab)
        if any(p.first_widget is not None for p in doc):
            more = QPushButton("🔍 Chercher d'autres cases")
            more.setToolTip("Si une case du document n'est pas en bleu, PDF Facile peut essayer de la trouver")
            more.clicked.connect(self.detectRequested.emit)
            v.addSpacing(10)
            v.addWidget(more)
        v.addStretch(1)
        self.scroll.setWidget(self.container)

    @staticmethod
    def _label(text):
        lab = QLabel(f"<b>{html.escape(text)}</b>")
        lab.setWordWrap(True)
        return lab

    def focus_field(self, xref):
        ed = self.editors.get(xref)
        if ed is None:
            return
        self.scroll.ensureWidgetVisible(ed, 20, 60)
        ed.setFocus()
        if isinstance(ed, QCheckBox):
            ed.toggle()
        elif isinstance(ed, QRadioButton):
            ed.setChecked(True)
        elif isinstance(ed, QComboBox):
            ed.showPopup()


# ----------------------------------------------------------------------------
# Dialogues
# ----------------------------------------------------------------------------
class TourDialog(QDialog):
    def __init__(self, parent, show_again):
        super().__init__(parent)
        self.setWindowTitle("Guide de démarrage")
        self.setMinimumWidth(640)
        self.i = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 28, 34, 22)
        lay.setSpacing(14)
        self.emoji = QLabel()
        self.emoji.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 19pt; font-weight: 700;")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setStyleSheet("font-size: 13pt; line-height: 140%;")
        self.text.setMinimumHeight(150)
        self.dots = QLabel()
        self.dots.setAlignment(Qt.AlignCenter)
        self.dots.setStyleSheet("font-size: 15pt; color: #2563eb;")
        for w in (self.emoji, self.title, self.text, self.dots):
            lay.addWidget(w)
        self.again = QCheckBox("Afficher ce guide à chaque démarrage")
        self.again.setChecked(show_again)
        lay.addWidget(self.again, alignment=Qt.AlignCenter)
        row = QHBoxLayout()
        skip = QPushButton("Passer le guide")
        skip.clicked.connect(self.accept)
        self.prev = QPushButton("◀  Précédent")
        self.prev.clicked.connect(lambda: self.go(self.i - 1))
        self.next = QPushButton()
        self.next.setObjectName("primary")
        self.next.clicked.connect(self._next)
        row.addWidget(skip)
        row.addStretch(1)
        row.addWidget(self.prev)
        row.addWidget(self.next)
        lay.addLayout(row)
        self.go(0)

    def go(self, i):
        self.i = max(0, min(len(TOUR_STEPS) - 1, i))
        e, t, x = TOUR_STEPS[self.i]
        self.emoji.setPixmap(emoji_icon(e, 96).pixmap(80, 80))
        self.title.setText(t)
        self.text.setText(x)
        self.dots.setText("  ".join("●" if k == self.i else "○" for k in range(len(TOUR_STEPS))))
        self.prev.setEnabled(self.i > 0)
        last = self.i == len(TOUR_STEPS) - 1
        self.next.setText("C'est parti !" if last else "Suivant  ▶")

    def _next(self):
        if self.i == len(TOUR_STEPS) - 1:
            self.accept()
        else:
            self.go(self.i + 1)


class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 210)
        self.setCursor(Qt.CrossCursor)
        self.strokes = []
        self.color = QColor("#0b2a6f")

    def clear(self):
        self.strokes = []
        self.update()

    def is_empty(self):
        return not self.strokes

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.strokes.append([e.position()])
            self.update()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.strokes:
            self.strokes[-1].append(e.position())
            self.update()

    def _paint_strokes(self, p):
        pen = QPen(self.color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for s in self.strokes:
            if len(s) == 1:
                p.setBrush(self.color)
                p.drawEllipse(s[0], 1.5, 1.5)
                p.setBrush(Qt.NoBrush)
                continue
            path = QPainterPath(s[0])
            for i in range(1, len(s) - 1):
                mid = (s[i] + s[i + 1]) / 2
                path.quadTo(s[i], mid)
            path.lineTo(s[-1])
            p.drawPath(path)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QPen(QColor("#94a3b8"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        y = int(self.height() * 0.72)
        p.setPen(QPen(QColor("#cbd5e1"), 1, Qt.DashLine))
        p.drawLine(30, y, self.width() - 30, y)
        p.setPen(QColor("#94a3b8"))
        p.drawText(12, y + 5, "✕")
        if not self.strokes:
            f = p.font()
            f.setPointSize(14)
            p.setFont(f)
            p.drawText(self.rect().adjusted(0, 0, 0, -60), Qt.AlignCenter,
                       "Signez ici en maintenant le bouton gauche de la souris")
        self._paint_strokes(p)

    def to_image(self, factor=4):
        pts = [pt for s in self.strokes for pt in s]
        x0 = min(p.x() for p in pts) - 4
        y0 = min(p.y() for p in pts) - 4
        x1 = max(p.x() for p in pts) + 4
        y1 = max(p.y() for p in pts) + 4
        w, h = max(8, x1 - x0), max(8, y1 - y0)
        img = QImage(int(w * factor), int(h * factor), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(factor, factor)
        p.translate(-x0, -y0)
        self._paint_strokes(p)
        p.end()
        return img


class SignatureDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Ma signature")
        self.result_image = None
        self.sig_dir = data_dir("signatures")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        self.saved_box = QGroupBox("Utiliser une signature enregistrée (cliquez dessus)")
        self.saved_lay = QHBoxLayout(self.saved_box)
        lay.addWidget(self.saved_box)
        self._fill_saved()

        lay.addWidget(QLabel("<b>Dessiner une nouvelle signature :</b>"))
        self.pad = SignaturePad()
        lay.addWidget(self.pad)
        row = QHBoxLayout()
        blue = QRadioButton("Encre bleue")
        black = QRadioButton("Encre noire")
        blue.setChecked(True)
        blue.toggled.connect(lambda c: c and self._set_color("#0b2a6f"))
        black.toggled.connect(lambda c: c and self._set_color("#111111"))
        clear = QPushButton("🧽 Effacer")
        clear.clicked.connect(self.pad.clear)
        imp = QPushButton("🖼 Importer une photo de ma signature…")
        imp.clicked.connect(self._import)
        for w in (blue, black):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(clear)
        row.addWidget(imp)
        lay.addLayout(row)
        self.keep = QCheckBox("Garder cette signature pour la prochaine fois")
        self.keep.setChecked(True)
        lay.addWidget(self.keep)
        btns = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Placer la signature  ▶")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self._use_drawn)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _set_color(self, c):
        self.pad.color = QColor(c)
        self.pad.update()

    def _fill_saved(self):
        while self.saved_lay.count():
            it = self.saved_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        files = sorted((f for f in os.listdir(self.sig_dir) if f.lower().endswith(".png")), reverse=True)
        for f in files[:5]:
            path = os.path.join(self.sig_dir, f)
            img = QImage(path)
            if img.isNull():
                continue
            col = QVBoxLayout()
            b = QToolButton()
            thumb = QPixmap(180, 70)
            thumb.fill(QColor("white"))
            p = QPainter(thumb)
            sc = img.scaled(170, 62, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawImage((180 - sc.width()) // 2, (70 - sc.height()) // 2, sc)
            p.end()
            b.setIcon(QIcon(thumb))
            b.setIconSize(QSize(180, 70))
            b.setToolTip("Utiliser cette signature")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, im=img: self._finish(im, save=False))
            d = QPushButton("Supprimer")
            d.setObjectName("link")
            d.clicked.connect(lambda _=False, pa=path: self._delete(pa))
            col.addWidget(b)
            col.addWidget(d, alignment=Qt.AlignCenter)
            wrap = QWidget()
            wrap.setLayout(col)
            self.saved_lay.addWidget(wrap)
        self.saved_lay.addStretch(1)
        self.saved_box.setVisible(bool(files))

    def _delete(self, path):
        if ask(self, "Supprimer cette signature enregistrée ?",
               [("Supprimer", QMessageBox.DestructiveRole), ("Annuler", QMessageBox.RejectRole)]) == 0:
            try:
                os.remove(path)
            except OSError:
                pass
            self._fill_saved()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choisir la photo de votre signature", documents_dir(), IMAGE_FILTER)
        if not path:
            return
        img = load_image_file(path, 1600)
        if img is None:
            warn(self, "Impossible de lire cette image.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            img = make_white_transparent(img)
        finally:
            QApplication.restoreOverrideCursor()
        self._finish(img, save=self.keep.isChecked())

    def _use_drawn(self):
        if self.pad.is_empty():
            warn(self, "Dessinez d'abord votre signature dans le cadre blanc,\n"
                       "ou choisissez une signature enregistrée.")
            return
        self._finish(self.pad.to_image(), save=self.keep.isChecked())

    def _finish(self, img, save):
        if save:
            name = datetime.datetime.now().strftime("signature_%Y%m%d_%H%M%S.png")
            img.save(os.path.join(self.sig_dir, name), "PNG")
        self.result_image = img
        self.accept()


class CompressDialog(QDialog):
    def __init__(self, parent, settings, size):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Réduire la taille du fichier")
        self.setMinimumWidth(680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        head = QLabel(f"Taille actuelle du document : <b>{human_size(size)}</b>")
        head.setStyleSheet("font-size: 13pt;")
        lay.addWidget(head)
        hint = QLabel("Utile quand un site ou un e-mail refuse un fichier trop lourd. "
                      "Votre document actuel n'est pas modifié : une copie plus légère sera enregistrée.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        box = QGroupBox("Que voulez-vous ?")
        bl = QVBoxLayout(box)
        row = QHBoxLayout()
        self.m_target = QRadioButton("Ne pas dépasser :")
        self.target = QComboBox()
        self.target.setEditable(True)
        for v in ("0,5", "1", "2", "3", "5", "10", "20"):
            self.target.addItem(v)
        self.target.setCurrentText(str(settings.value("compress_target", "2")))
        self.target.setFixedWidth(90)
        self.target.editTextChanged.connect(lambda *_: self.m_target.setChecked(True))
        row.addWidget(self.m_target)
        row.addWidget(self.target)
        row.addWidget(QLabel("Mo"))
        row.addStretch(1)
        bl.addLayout(row)
        ex = QLabel("(le site ou l'administration indique souvent la taille maximum, par exemple « 2 Mo »)")
        ex.setObjectName("hint")
        ex.setWordWrap(True)
        ex.setContentsMargins(24, 0, 0, 0)
        bl.addWidget(ex)
        self.m_light = QRadioButton("Réduire un peu — qualité presque identique")
        self.m_strong = QRadioButton("Réduire beaucoup — qualité suffisante pour l'écran et l'impression")
        bl.addWidget(self.m_light)
        bl.addWidget(self.m_strong)
        {"light": self.m_light, "strong": self.m_strong}.get(settings.value("compress_mode", "target"),
                                                            self.m_target).setChecked(True)
        lay.addWidget(box)
        self.gray = QCheckBox("Passer aussi en noir et blanc (encore plus léger)")
        self.gray.setChecked(settings.value("compress_gray", False, type=bool))
        lay.addWidget(self.gray)

        btns = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("🗜️  Réduire la taille")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def target_bytes(self):
        try:
            v = float(self.target.currentText().replace(",", ".").replace("Mo", "").strip())
        except ValueError:
            return None
        return int(v * 1024 * 1024) if v > 0 else None

    def mode(self):
        return "light" if self.m_light.isChecked() else "strong" if self.m_strong.isChecked() else "target"

    def _accept(self):
        if self.mode() == "target" and self.target_bytes() is None:
            warn(self, "Indiquez la taille maximum en Mo, par exemple 2 ou 0,5.")
            return
        self.settings.setValue("compress_target", self.target.currentText())
        self.settings.setValue("compress_mode", self.mode())
        self.settings.setValue("compress_gray", self.gray.isChecked())
        self.accept()


class PrintOptionsDialog(QDialog):
    """Fenêtre d'impression simple, en français."""

    def __init__(self, parent, settings, page_count, current):
        super().__init__(parent)
        self.settings, self.page_count, self.current = settings, page_count, current
        self.printer = None
        self.setWindowTitle("Imprimer")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("<b>Imprimante :</b>"))
        self.printers = QComboBox()
        names = QPrinterInfo.availablePrinterNames()
        default = QPrinterInfo.defaultPrinterName()
        for n in names:
            self.printers.addItem(("⭐ " if n == default else "") + n, n)
        last = settings.value("print_printer", default)
        idx = self.printers.findData(last if last in names else default)
        self.printers.setCurrentIndex(max(0, idx))
        self.printers.currentIndexChanged.connect(self._printer_changed)
        lay.addWidget(self.printers)

        box = QGroupBox("Pages à imprimer")
        bl = QVBoxLayout(box)
        self.p_all = QRadioButton(f"Tout le document ({page_count} page{'s' if page_count > 1 else ''})")
        self.p_cur = QRadioButton(f"Seulement la page affichée (page {current + 1})")
        rowp = QHBoxLayout()
        self.p_some = QRadioButton("Les pages :")
        self.p_text = QLineEdit()
        self.p_text.setPlaceholderText("ex. 1-3, 5")
        self.p_text.textEdited.connect(lambda *_: self.p_some.setChecked(True))
        rowp.addWidget(self.p_some)
        rowp.addWidget(self.p_text, 1)
        self.p_all.setChecked(True)
        bl.addWidget(self.p_all)
        bl.addWidget(self.p_cur)
        bl.addLayout(rowp)
        lay.addWidget(box)

        cbox = QGroupBox("Couleurs")
        cl = QVBoxLayout(cbox)
        self.c_color = QRadioButton("🎨  En couleur")
        self.c_gray = QRadioButton("⚫  En noir et blanc (économise l'encre de couleur)")
        (self.c_gray if settings.value("print_gray", False, type=bool) else self.c_color).setChecked(True)
        cl.addWidget(self.c_color)
        cl.addWidget(self.c_gray)
        lay.addWidget(cbox)

        row = QHBoxLayout()
        row.addWidget(QLabel("Nombre d'exemplaires :"))
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        row.addWidget(self.copies)
        row.addSpacing(20)
        self.duplex = QCheckBox("Recto verso")
        self.duplex.setChecked(settings.value("print_duplex", False, type=bool))
        row.addWidget(self.duplex)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QHBoxLayout()
        adv = QPushButton("Autres réglages…")
        adv.setToolTip("Ouvre la fenêtre d'impression de Windows (format du papier, qualité…)")
        adv.clicked.connect(self._advanced)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("🖨️  Imprimer")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        btns.addWidget(adv)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        if not names:
            self.printers.addItem("Aucune imprimante trouvée", "")
            self.printers.setEnabled(False)
            self.ok.setEnabled(False)
            adv.setEnabled(False)
        self._printer_changed()

    def _make_printer(self):
        printer = QPrinter(QPrinter.HighResolution)
        name = self.printers.currentData()
        if name:
            printer.setPrinterName(name)
        return printer

    def _printer_changed(self, *a):
        name = self.printers.currentData()
        info_ = QPrinterInfo.printerInfo(name) if name else None
        if info_ is not None and not info_.isNull():
            modes = info_.supportedDuplexModes()
            can = any(m in modes for m in (QPrinter.DuplexAuto, QPrinter.DuplexLongSide))
            self.duplex.setEnabled(can)
            self.duplex.setToolTip("" if can else "Cette imprimante ne sait pas imprimer en recto verso")
            if not can:
                self.duplex.setChecked(False)
            color = info_.supportedColorModes() if hasattr(info_, "supportedColorModes") else []
            if color and all(c == QPrinter.GrayScale for c in color):
                self.c_gray.setChecked(True)
                self.c_color.setEnabled(False)
                self.c_color.setToolTip("Cette imprimante n'imprime qu'en noir et blanc")
            else:
                self.c_color.setEnabled(True)

    def _advanced(self):
        printer = self.printer or self._make_printer()
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Réglages de l'imprimante")
        if dlg.exec() == QDialog.Accepted:
            self.printer = printer
            self.copies.setValue(max(1, printer.copyCount()))

    def pages(self):
        if self.p_cur.isChecked():
            return [self.current]
        if self.p_some.isChecked():
            return parse_page_ranges(self.p_text.text(), self.page_count)
        return list(range(self.page_count))

    def _accept(self):
        if self.pages() is None:
            warn(self, f"Je n'ai pas compris les pages à imprimer.\n\nÉcrivez par exemple « 2 » ou « 1-3, 5 » "
                       f"(le document a {self.page_count} pages).")
            return
        self.settings.setValue("print_printer", self.printers.currentData())
        self.settings.setValue("print_gray", self.c_gray.isChecked())
        self.settings.setValue("print_duplex", self.duplex.isChecked())
        self.accept()

    def configured_printer(self):
        printer = self.printer or self._make_printer()
        printer.setColorMode(QPrinter.GrayScale if self.c_gray.isChecked() else QPrinter.Color)
        if self.duplex.isEnabled():
            printer.setDuplex(QPrinter.DuplexAuto if self.duplex.isChecked() else QPrinter.DuplexNone)
        printer.setCopyCount(self.copies.value())
        return printer


class FileList(QListWidget):
    """Liste de fichiers réordonnable, qui accepte les fichiers glissés depuis l'Explorateur."""
    filesAdded = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAcceptDrops(True)
        self.setIconSize(QSize(60, 80))
        self.setSpacing(3)

    def add_files(self, paths):
        items = []
        for p in paths:
            if p.lower().endswith((".pdf",) + IMAGE_EXT):
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                self.addItem(it)
                items.append(it)
        if items:
            self.filesAdded.emit(items)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.add_files([u.toLocalFile() for u in e.mimeData().urls()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def paths(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]


class CombineDialog(QDialog):
    PV_W, PV_H = 360, 470

    def __init__(self, parent, images_mode=False):
        super().__init__(parent)
        self.images_mode = images_mode
        self.sources = {}          # chemin -> ("pdf", doc) | ("img", QImage) | ("locked"|"error", None)
        self.pv_page = 0
        self.setWindowTitle("Créer un PDF à partir de photos" if images_mode else "Combiner des fichiers")
        self.setMinimumSize(980, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lab = QLabel("Ajoutez les fichiers à réunir en un seul PDF (documents PDF ou photos). "
                     "Faites-les glisser dans la liste pour changer l'ordre.<br>"
                     "Cliquez sur un fichier pour voir son contenu à droite.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.list = FileList()
        self.list.filesAdded.connect(self._decorate)
        self.list.currentItemChanged.connect(lambda *_: self._show_preview(reset=True))
        left.addWidget(self.list, 1)
        self.total = QLabel()
        self.total.setObjectName("hint")
        left.addWidget(self.total)
        row.addLayout(left, 1)
        col = QVBoxLayout()
        for text, slot in (("➕ Ajouter des fichiers…", self._add), ("⬆ Monter", lambda: self._move(-1)),
                           ("⬇ Descendre", lambda: self._move(1)), ("✖ Retirer", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            col.addWidget(b)
        col.addStretch(1)
        row.addLayout(col)

        pv = QFrame()
        pv.setObjectName("sidepanel")
        pl = QVBoxLayout(pv)
        pl.setContentsMargins(10, 10, 10, 10)
        self.pv_title = QLabel("<b>Aperçu</b>")
        self.pv_title.setWordWrap(True)
        pl.addWidget(self.pv_title)
        self.pv_img = QLabel()
        self.pv_img.setAlignment(Qt.AlignCenter)
        self.pv_img.setFixedSize(self.PV_W, self.PV_H)
        self.pv_img.setStyleSheet("background: #e5e7eb; border-radius: 6px; color: #4b5563;")
        pl.addWidget(self.pv_img)
        nav = QHBoxLayout()
        self.pv_prev = QPushButton("◀")
        self.pv_prev.clicked.connect(lambda: self._step(-1))
        self.pv_label = QLabel()
        self.pv_label.setAlignment(Qt.AlignCenter)
        self.pv_next = QPushButton("▶")
        self.pv_next.clicked.connect(lambda: self._step(1))
        nav.addWidget(self.pv_prev)
        nav.addWidget(self.pv_label, 1)
        nav.addWidget(self.pv_next)
        pl.addLayout(nav)
        row.addWidget(pv)
        lay.addLayout(row, 1)

        btns = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("📚 Créer le document")
        self.ok.setObjectName("primary")
        self.ok.clicked.connect(self.accept)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.list.model().rowsInserted.connect(self._upd)
        self.list.model().rowsRemoved.connect(self._upd)
        self._upd()
        self._show_preview()
        QTimer.singleShot(0, self._add)

    # sources
    def _source(self, path):
        if path not in self.sources:
            try:
                if path.lower().endswith(IMAGE_EXT):
                    img = load_image_file(path, 1400)
                    self.sources[path] = ("img", img) if img is not None else ("error", None)
                else:
                    doc = pymupdf.open(path)
                    if doc.needs_pass:
                        self.sources[path] = ("locked", None)
                        doc.close()
                    else:
                        self.sources[path] = ("pdf", doc)
            except Exception:
                self.sources[path] = ("error", None)
        return self.sources[path]

    def _page_count(self, path):
        kind, obj = self._source(path)
        return obj.page_count if kind == "pdf" else (1 if kind == "img" else 0)

    def _render(self, path, index, w, h):
        kind, obj = self._source(path)
        if kind == "pdf":
            page = obj[max(0, min(index, obj.page_count - 1))]
            z = min(w / page.rect.width, h / page.rect.height) * self.devicePixelRatioF()
            img = pix_to_qimage(page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False))
        elif kind == "img":
            img = obj.scaled(int(w * self.devicePixelRatioF()), int(h * self.devicePixelRatioF()),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            return None
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        return pm

    def _decorate(self, items):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for it in items:
                path = it.data(Qt.UserRole)
                kind, _ = self._source(path)
                name = os.path.basename(path)
                if kind == "locked":
                    it.setIcon(emoji_icon("🔒"))
                    it.setText(f"{name}\nprotégé par un mot de passe")
                elif kind == "error":
                    it.setIcon(emoji_icon("⚠️"))
                    it.setText(f"{name}\nillisible — sera ignoré")
                else:
                    pm = self._render(path, 0, 60, 80)
                    if pm is not None:
                        it.setIcon(QIcon(pm))
                    n = self._page_count(path)
                    it.setText(f"{name}\n{'photo' if kind == 'img' else f'{n} page' + ('s' if n > 1 else '')}")
        finally:
            QApplication.restoreOverrideCursor()
        self.list.setCurrentItem(items[-1])
        self._upd()

    # aperçu
    def _show_preview(self, reset=False):
        it = self.list.currentItem()
        if reset:
            self.pv_page = 0
        if it is None:
            self.pv_title.setText("<b>Aperçu</b>")
            self.pv_img.setPixmap(QPixmap())
            self.pv_img.setText("Ajoutez des fichiers pour\nvoir leur aperçu ici.")
            self.pv_label.setText("")
            self.pv_prev.setEnabled(False)
            self.pv_next.setEnabled(False)
            return
        path = it.data(Qt.UserRole)
        kind, _ = self._source(path)
        n = self._page_count(path)
        self.pv_title.setText(f"<b>{html.escape(os.path.basename(path))}</b>")
        pm = self._render(path, self.pv_page, self.PV_W - 16, self.PV_H - 16)
        if pm is None:
            self.pv_img.setPixmap(QPixmap())
            self.pv_img.setText("🔒 Document protégé :\nle mot de passe sera demandé\nau moment de combiner."
                                if kind == "locked" else "⚠️ Ce fichier ne peut pas être lu.")
        else:
            self.pv_img.setPixmap(pm)
        self.pv_label.setText(f"Page {self.pv_page + 1} sur {n}" if n else "")
        self.pv_prev.setEnabled(self.pv_page > 0)
        self.pv_next.setEnabled(self.pv_page < n - 1)

    def _step(self, d):
        self.pv_page += d
        self._show_preview()

    def _upd(self, *a):
        paths = self.list.paths()
        self.ok.setEnabled(bool(paths))
        total = sum(self._page_count(p) for p in paths if p in self.sources)
        self.total.setText(f"{len(paths)} fichier(s) — {total} page(s) au total" if paths else "")
        if not paths:
            self._show_preview()

    def _add(self):
        flt = f"{IMAGE_FILTER};;PDF et images (*.pdf *.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff)" \
            if self.images_mode else \
            f"PDF et images (*.pdf *.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff);;{PDF_FILTER};;{IMAGE_FILTER}"
        paths, _ = QFileDialog.getOpenFileNames(self, "Choisir les fichiers", documents_dir(), flt)
        self.list.add_files(paths)

    def _move(self, d):
        r = self.list.currentRow()
        if r < 0 or not (0 <= r + d < self.list.count()):
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def _remove(self):
        r = self.list.currentRow()
        if r >= 0:
            self.list.takeItem(r)

    def done(self, r):
        for kind, obj in self.sources.values():
            if kind == "pdf":
                obj.close()
        super().done(r)


# ----------------------------------------------------------------------------
# Fenêtre principale
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.doc = None
        self.path = None
        self.display_name = ""
        self.undo_stack, self.redo_stack = [], []
        self.state_id, self.saved_id, self._next_id = 0, 0, 1
        self.current = 0
        self.zoom = 1.0
        self.fit_mode = "width"
        self.mode = None
        self._form_key = None
        self._used_font = False
        self._ghost_bytes = None
        self._ghost_text = None
        self.page_images = []
        self._thumb_queue = []
        self._goto_guard = False
        self._editor = None
        self._editor_pt = None
        self._editing_xref = None
        self.page_objects = []
        self._field_ed = None
        self._field_hint_done = False

        self.setWindowTitle(APP_NAME)
        ico = resource_path("icon.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        self.resize(1320, 880)
        self.setAcceptDrops(True)
        self._build_ui()
        self._shortcuts()
        self._update_state()
        if initial_path:
            QTimer.singleShot(50, lambda: self.open_path(initial_path))
        QTimer.singleShot(400, self.maybe_show_tour)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # --- barre du haut
        top = QWidget()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 6, 8, 6)
        tl.setSpacing(2)
        self.b_open = big_button("📂", "Ouvrir", "Ouvrir un document PDF (Ctrl+O)", self.open_dialog)
        self.b_save = big_button("💾", "Enregistrer", "Enregistrer les modifications (Ctrl+S)", self.save)
        save_menu = QMenu(self.b_save)
        save_menu.addAction("💾  Enregistrer", self.save)
        save_menu.addAction("📑  Enregistrer sous… (garder l'original)", self.save_as)
        save_menu.addAction("🗜️  Enregistrer une version plus légère…", self.compress_doc)
        self.b_save.setMenu(save_menu)
        self.b_save.setPopupMode(QToolButton.MenuButtonPopup)
        self.b_print = big_button("🖨️", "Imprimer", "Imprimer le document (Ctrl+P)", self.print_doc)
        self.b_undo = big_button("↩️", "Annuler", "Annuler la dernière action (Ctrl+Z)", self.undo)
        self.b_redo = big_button("↪️", "Rétablir", "Refaire l'action annulée (Ctrl+Y)", self.redo)
        self.b_sign = big_button("✍️", "Signer", "Dessiner ou choisir votre signature, puis la placer", self.sign)
        self.b_text = big_button("🔤", "Écrire\ndu texte", "Écrire du texte n'importe où sur la page", self.add_text)
        self.b_check = big_button("✔️", "Cocher\nune case", "Cocher une case d'un formulaire (cliquez sur la case)",
                                  self.check_mode)
        self.b_img = big_button("🖼️", "Ajouter\nune image", "Placer une image ou une photo sur la page", self.add_image)
        self.b_rmimg = big_button("✂️", "Retirer\nune image", "Retirer une image de la page", self.remove_image_mode)
        self.b_form = big_button("📝", "Formulaire", "Afficher / masquer la liste des cases à remplir", self.toggle_form)
        self.b_form.setCheckable(True)
        self.b_compress = big_button("🗜️", "Réduire\nla taille",
                                     "Créer une copie plus légère du document (pour l'envoyer par e-mail ou sur un site)",
                                     self.compress_doc)
        self.b_combine = big_button("📚", "Combiner", "Réunir plusieurs PDF ou photos en un seul document", self.combine)
        self.b_help = big_button("❓", "Aide", "Guide et astuces")
        help_menu = QMenu(self.b_help)
        help_menu.addAction("🎓  Revoir le guide de démarrage", self.show_tour)
        help_menu.addAction("💡  Afficher les astuces", self.enable_tips)
        help_menu.addSeparator()
        help_menu.addAction("ℹ️  À propos", self.about)
        self.b_help.setMenu(help_menu)
        self.b_help.setPopupMode(QToolButton.InstantPopup)

        for w in (self.b_open, self.b_save, self.b_print, separator(), self.b_undo, self.b_redo, separator(),
                  self.b_sign, self.b_text, self.b_check, self.b_img, self.b_rmimg, self.b_form, separator(), self.b_combine, self.b_compress):
            tl.addWidget(w)
        tl.addStretch(1)
        tl.addWidget(self.b_help)
        rl.addWidget(top)

        self.tipbar = TipBar()
        self.tipbar.disabled.connect(self.disable_tips)
        self.tipbar.hide()
        rl.addWidget(self.tipbar)
        self.modebar = ModeBar()
        self.modebar.cancelled.connect(lambda: self.set_mode(None))
        rl.addWidget(self.modebar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_workspace())
        rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.doc_buttons = [self.b_save, self.b_print, self.b_compress, self.b_sign, self.b_text, self.b_check, self.b_img,
                            self.b_rmimg, self.b_form]

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._thumb_step)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self.render_current)

    def _build_welcome(self):
        w = QWidget()
        w.setObjectName("welcome")
        outer = QVBoxLayout(w)
        outer.addStretch(1)
        box = QVBoxLayout()
        box.setSpacing(14)
        hello = QLabel("Bonjour ! 👋")
        hello.setStyleSheet("font-size: 28pt; font-weight: 700;")
        sub = QLabel("Que voulez-vous faire ?")
        sub.setStyleSheet("font-size: 15pt; color: #4b5563;")
        box.addWidget(hello, alignment=Qt.AlignHCenter)
        box.addWidget(sub, alignment=Qt.AlignHCenter)
        box.addSpacing(10)
        for emoji, text, slot in (("📂", "Ouvrir un document PDF", self.open_dialog),
                                  ("📚", "Réunir plusieurs PDF en un seul", self.combine),
                                  ("📷", "Créer un PDF à partir de photos", lambda: self.combine(images=True))):
            b = QPushButton(f"  {text}")
            b.setObjectName("huge")
            b.setIcon(emoji_icon(emoji))
            b.setIconSize(QSize(34, 34))
            b.setMinimumWidth(460)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            box.addWidget(b, alignment=Qt.AlignHCenter)
        drop = QLabel("Vous pouvez aussi faire glisser un fichier PDF dans cette fenêtre.")
        drop.setObjectName("hint")
        box.addWidget(drop, alignment=Qt.AlignHCenter)
        box.addSpacing(16)
        self.recent_title = QLabel("<b>Documents récents</b> (cliquez pour ouvrir)")
        box.addWidget(self.recent_title, alignment=Qt.AlignHCenter)
        self.recent_list = QListWidget()
        self.recent_list.setFixedSize(560, 190)
        self.recent_list.setCursor(Qt.PointingHandCursor)
        self.recent_list.itemClicked.connect(lambda it: self.open_path(it.data(Qt.UserRole)))
        box.addWidget(self.recent_list, alignment=Qt.AlignHCenter)
        outer.addLayout(box)
        outer.addStretch(2)
        self._fill_recent()
        return w

    def _build_workspace(self):
        w = QWidget()
        w.setObjectName("workspace")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # pages
        left = QFrame()
        left.setObjectName("sidepanel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 10)
        t = QLabel("📄 Pages")
        t.setObjectName("paneltitle")
        ll.addWidget(t)
        h = QLabel("Glissez les pages pour changer leur ordre.")
        h.setObjectName("hint")
        h.setWordWrap(True)
        ll.addWidget(h)
        self.thumbs = ThumbList()
        self.thumbs.currentRowChanged.connect(self._on_thumb_row)
        self.thumbs.orderChanged.connect(self._on_thumbs_reordered)
        self.thumbs.deleteRequested.connect(self.delete_pages)
        ll.addWidget(self.thumbs, 1)
        gw = QWidget()
        gw.setObjectName("pagebtn")
        g = QGridLayout(gw)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(6)

        def pb(text, tip, slot):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b
        self.p_left = pb("↺ Tourner à gauche", "Tourner la page d'un quart de tour vers la gauche", lambda: self.rotate(-90))
        self.p_right = pb("↻ Tourner à droite", "Tourner la page d'un quart de tour vers la droite", lambda: self.rotate(90))
        self.p_up = pb("⬆ Monter", "Déplacer la page vers le début", lambda: self.move_pages(-1))
        self.p_down = pb("⬇ Descendre", "Déplacer la page vers la fin", lambda: self.move_pages(1))
        self.p_add = pb("➕ Ajouter…", "Ajouter une page", lambda: None)
        add_menu = QMenu(self.p_add)
        add_menu.addAction("📄  Une page blanche", self.add_blank_page)
        add_menu.addAction("📑  Les pages d'un autre PDF…", self.insert_pdf)
        add_menu.addAction("📷  Une photo ou image (nouvelle page)…", self.insert_image_pages)
        self.p_add.setMenu(add_menu)
        self.p_del = pb("🗑 Supprimer", "Supprimer la ou les pages sélectionnées (Suppr)", self.delete_pages)
        self.p_extract = pb("📤 Enregistrer ces pages à part…",
                            "Créer un nouveau PDF avec seulement les pages sélectionnées", self.extract_pages)
        g.addWidget(self.p_left, 0, 0)
        g.addWidget(self.p_right, 0, 1)
        g.addWidget(self.p_up, 1, 0)
        g.addWidget(self.p_down, 1, 1)
        g.addWidget(self.p_add, 2, 0)
        g.addWidget(self.p_del, 2, 1)
        g.addWidget(self.p_extract, 3, 0, 1, 2)
        ll.addWidget(gw)
        left.setMinimumWidth(330)
        split.addWidget(left)

        # centre
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.scroll = PageScroll()
        self.view = PageView()
        self.scroll.setWidget(self.view)
        self.scroll.nextPage.connect(lambda: self.go_to(self.current + 1))
        self.scroll.prevPage.connect(lambda: self.go_to(self.current - 1, bottom=True))
        self.scroll.resized.connect(lambda: self.fit_mode and self._render_timer.start())
        self.view.placed.connect(self.on_placed)
        self.view.imageClicked.connect(self.on_image_clicked)
        self.view.fieldClicked.connect(self.on_field_clicked)
        self.view.typeAt.connect(self.on_type_at)
        self.view.checkAt.connect(self.on_check_at)
        self.view.objectClicked.connect(self.on_object_clicked)
        self.view.objectMoved.connect(self.on_object_moved)
        self.view.objectMenu.connect(self.on_object_menu)
        self.view.fieldMenu.connect(self.on_field_menu)
        self.view.backgroundClicked.connect(lambda: self._commit_field_editor())
        self.view.zoomWheel.connect(lambda d: self.zoom_by(1.1 if d > 0 else 1 / 1.1))
        cl.addWidget(self.scroll, 1)

        nav = QWidget()
        nav.setObjectName("navbar")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(10, 6, 10, 6)
        self.n_prev = QPushButton("◀ Page précédente")
        self.n_prev.clicked.connect(lambda: self.go_to(self.current - 1))
        self.n_next = QPushButton("Page suivante ▶")
        self.n_next.clicked.connect(lambda: self.go_to(self.current + 1))
        self.n_spin = QSpinBox()
        self.n_spin.setMinimum(1)
        self.n_spin.setKeyboardTracking(False)
        self.n_spin.valueChanged.connect(lambda v: self.go_to(v - 1))
        self.n_total = QLabel()
        nl.addWidget(self.n_prev)
        nl.addWidget(QLabel("  Page"))
        nl.addWidget(self.n_spin)
        nl.addWidget(self.n_total)
        nl.addWidget(self.n_next)
        nl.addStretch(1)
        zo = QPushButton("−")
        zo.setToolTip("Réduire (Ctrl + molette)")
        zo.setFixedWidth(44)
        zo.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        self.zoom_label = QLabel("100 %")
        self.zoom_label.setMinimumWidth(60)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        zi = QPushButton("+")
        zi.setToolTip("Agrandir (Ctrl + molette)")
        zi.setFixedWidth(44)
        zi.clicked.connect(lambda: self.zoom_by(1.2))
        fw = QPushButton("↔ Largeur")
        fw.setToolTip("Adapter à la largeur de la fenêtre")
        fw.clicked.connect(lambda: self.set_fit("width"))
        fp = QPushButton("⬜ Page entière")
        fp.setToolTip("Voir la page entière")
        fp.clicked.connect(lambda: self.set_fit("page"))
        for x in (zo, self.zoom_label, zi, fw, fp):
            nl.addWidget(x)
        cl.addWidget(nav)
        split.addWidget(center)

        # formulaire
        self.form_panel = FormPanel()
        self.form_panel.changed.connect(self.on_field_changed)
        self.form_panel.writeRequested.connect(self.add_text)
        self.form_panel.detectRequested.connect(self.detect_form_fields)
        self.form_panel.setMinimumWidth(300)
        self.form_panel.hide()
        split.addWidget(self.form_panel)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 780, 330])
        lay.addWidget(split)
        return w

    def _shortcuts(self):
        for seq, fn in ((QKeySequence.Open, self.open_dialog), (QKeySequence.Save, self.save),
                        (QKeySequence("Ctrl+Shift+S"), self.save_as), (QKeySequence.Print, self.print_doc),
                        (QKeySequence.Undo, self.undo), (QKeySequence("Ctrl+Y"), self.redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.redo), (QKeySequence(Qt.Key_Escape), lambda: self.set_mode(None)),
                        (QKeySequence(Qt.Key_PageDown), lambda: self.go_to(self.current + 1)),
                        (QKeySequence(Qt.Key_PageUp), lambda: self.go_to(self.current - 1)),
                        (QKeySequence.ZoomIn, lambda: self.zoom_by(1.2)), (QKeySequence("Ctrl+="), lambda: self.zoom_by(1.2)),
                        (QKeySequence.ZoomOut, lambda: self.zoom_by(1 / 1.2))):
            QShortcut(seq, self, fn)

    # ------------------------------------------------------------- état
    def _flush_all(self):
        """Valide la saisie en cours (formulaire ou texte libre)."""
        self._commit_field_editor()
        self.form_panel.flush()
        if self._editor is not None:
            self._commit_editor()

    def is_dirty(self):
        return self.doc is not None and self.state_id != self.saved_id

    def _update_state(self):
        has = self.doc is not None
        for b in self.doc_buttons:
            b.setEnabled(has)
        self.b_undo.setEnabled(has and bool(self.undo_stack))
        self.b_redo.setEnabled(has and bool(self.redo_stack))
        if has:
            star = " •  modifié" if self.is_dirty() else ""
            self.setWindowTitle(f"{self.display_name}{star} — {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)

    def snapshot(self):
        """Mémorise l'état actuel pour pouvoir l'annuler."""
        if self._editor is not None:
            self._commit_editor()
        self.undo_stack.append((self.doc.tobytes(), self.state_id))
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.state_id = self._next_id
        self._next_id += 1
        self._form_key = None
        self._update_state()

    def drop_snapshot(self):
        """L'opération n'a rien changé : on oublie le dernier point d'annulation."""
        if self.undo_stack:
            _, self.state_id = self.undo_stack.pop()
            self._update_state()

    def _reload(self, data):
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)
        self.current = min(self.current, self.doc.page_count - 1)
        self.rebuild_thumbs()
        self.go_to(self.current, force=True)

    def undo(self):
        if not self.doc:
            return
        self._flush_all()
        if not self.undo_stack:
            return
        self.set_mode(None)
        self.redo_stack.append((self.doc.tobytes(), self.state_id))
        data, self.state_id = self.undo_stack.pop()
        self._form_key = None
        self._reload(data)
        self._update_state()
        self.flash("↩️ Action annulée.")

    def redo(self):
        if not self.doc or not self.redo_stack:
            return
        self.set_mode(None)
        self.undo_stack.append((self.doc.tobytes(), self.state_id))
        data, self.state_id = self.redo_stack.pop()
        self._form_key = None
        self._reload(data)
        self._update_state()
        self.flash("↪️ Action rétablie.")

    def flash(self, text, kind="success"):
        if self.mode is None:
            self.modebar.show_message(text, kind=kind, cancel=False, timeout=4000)

    # --------------------------------------------------------- documents
    def _open_any(self, path):
        """Ouvre un PDF (ou une image) et renvoie un document PDF prêt, ou None."""
        if path.lower().endswith(IMAGE_EXT):
            img = load_image_file(path)
            if img is None:
                warn(self, f"Impossible de lire l'image « {os.path.basename(path)} ».")
                return None
            doc = pymupdf.open()
            add_image_as_page(doc, img)
            return doc
        try:
            with open(path, "rb") as f:
                data = f.read()
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            warn(self, f"Impossible d'ouvrir « {os.path.basename(path)} ».",
                 detail="Le fichier est peut-être endommagé, ou ce n'est pas un PDF.")
            return None
        if doc.needs_pass:
            for attempt in range(3):
                pwd, ok = QInputDialog.getText(
                    self, "Document protégé",
                    f"« {os.path.basename(path)} » est protégé par un mot de passe.\nTapez le mot de passe :",
                    QLineEdit.Password)
                if not ok:
                    return None
                if doc.authenticate(pwd):
                    break
                warn(self, "Mot de passe incorrect.")
            else:
                return None
            doc = pymupdf.open("pdf", doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE))
        if doc.page_count == 0:
            warn(self, "Ce document ne contient aucune page.")
            return None
        return doc

    def open_dialog(self):
        start = self.settings.value("last_dir", documents_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir un document PDF", start,
                                              f"{PDF_FILTER};;Tous les fichiers (*.*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        if not path or not os.path.exists(path):
            warn(self, "Ce fichier est introuvable. Il a peut-être été déplacé ou supprimé.")
            self._remove_recent(path)
            return
        if not self.maybe_save():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            doc = self._open_any(path)
        finally:
            QApplication.restoreOverrideCursor()
        if doc is None:
            return
        is_pdf = path.lower().endswith(".pdf")
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.set_document(doc, path if is_pdf else None,
                          os.path.basename(path) if is_pdf else os.path.splitext(os.path.basename(path))[0] + ".pdf",
                          dirty=not is_pdf)
        if is_pdf:
            self._add_recent(path)

    def set_document(self, doc, path, name, dirty=False):
        if self.doc is not None:
            self.doc.close()
        self.set_mode(None)
        self.doc, self.path, self.display_name = doc, path, name
        self.undo_stack, self.redo_stack = [], []
        self.state_id = self._next_id
        self._next_id += 1
        self.saved_id = -1 if dirty else self.state_id
        self._used_font = False
        self._form_key = None
        self.current = 0
        self.fit_mode = "width"
        self.stack.setCurrentIndex(1)
        has_form = any(p.first_widget is not None for p in doc)
        self.form_panel.hide()
        self.b_form.setChecked(False)
        self._field_ed = None
        self._field_hint_done = False
        self.rebuild_thumbs()
        self._update_state()
        QTimer.singleShot(0, lambda: self.go_to(0, force=True))
        if has_form:
            self.modebar.show_message("📝 Ce document contient des <b>cases à remplir</b> (en bleu). "
                                      "<b>Cliquez dans une case</b> et tapez votre réponse.",
                                      kind="mode", cancel=False, timeout=15000)
        QTimer.singleShot(150 if not has_form else 16000, self._offer_detection)
        if self.settings.value("tips", True, type=bool):
            n = int(self.settings.value("tip_index", 0))
            self.tipbar.show_tip(n)
            self.settings.setValue("tip_index", n + 1)

    def _offer_detection(self):
        """Si le PDF ressemble à un formulaire « à imprimer », proposer de trouver les cases."""
        if not self.doc or self.doc.page_count > 40:
            return
        widgets = [w for p in self.doc for w in p.widgets() or []]
        ours = bool(widgets) and all((w.field_name or "").startswith("pf_p") for w in widgets)
        if widgets and not ours:
            return                    # vrai formulaire : on ne propose rien automatiquement
        try:
            ctx, n = {}, 0
            for page in self.doc:
                if page.rotation == 0:
                    n += len(new_candidates(page, detect_fields(page, ctx)))
        except Exception:
            return
        if n < 3:
            return
        if not hasattr(self, "_detect_btn"):
            self._detect_btn = QPushButton("🔍 Trouver les cases")
            self._detect_btn.setObjectName("primary")
            self._detect_btn.clicked.connect(self.detect_form_fields)
        msg = (f"📝 PDF Facile a repéré <b>{n} cases de plus</b> qui ne sont pas encore remplissables." if ours else
               f"📝 Ce document ressemble à un formulaire à imprimer (environ <b>{n} cases</b>). "
               "PDF Facile peut les rendre remplissables sur l'ordinateur.")
        self.modebar.show_message(msg, kind="mode", cancel=False, extra=self._detect_btn)

    def detect_form_fields(self):
        if not self.doc:
            return
        self._flush_all()
        self.set_mode(None)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        total = 0
        try:
            self.snapshot()
            ctx = {}
            used = {w.field_name for p in self.doc for w in p.widgets() or []}
            for page in self.doc:
                rot = page.rotation
                if rot:
                    page.set_rotation(0)
                cands = new_candidates(page, detect_fields(page, ctx))
                add_detected_fields(self.doc, page, cands, used)
                if rot:
                    page.set_rotation(rot)
                total += len(cands)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            warn(self, "La recherche des cases a échoué.", detail=str(e))
            self.undo()
            return
        QApplication.restoreOverrideCursor()
        if not total:
            self.drop_snapshot()
            self.modebar.hide()
            info(self, "Aucune nouvelle case n'a été trouvée automatiquement.\n\n"
                       "Utilisez « Écrire du texte » et « Cocher une case » pour compléter le document.")
            return
        self.rebuild_thumbs()
        self.go_to(self.current, force=True)
        self._update_state()
        self.modebar.show_message(f"✅ <b>{total} cases trouvées</b> (en bleu). <b>Cliquez dans une case</b> pour écrire "
                                  "dedans. S'il en manque une, utilisez « Écrire du texte ».",
                                  kind="success", cancel=False, timeout=20000)

    def on_field_menu(self, xref, pos):
        page = self.doc[self.current]
        w = next((x for x in page.widgets() if x.xref == xref), None)
        if w is None:
            return
        m = QMenu(self)
        m.addAction("✏️  Remplir cette case", lambda: self.on_field_clicked(xref))
        if (w.field_name or "").startswith("pf_p"):
            m.addAction("🗑  Retirer cette case (trouvée par erreur)", lambda: self._remove_field(xref))
        m.exec(pos)

    def _remove_field(self, xref):
        self._flush_all()
        self.snapshot()
        page = self.doc[self.current]
        w = next((x for x in page.widgets() if x.xref == xref), None)
        if w is not None:
            page.delete_widget(w)
        self.render_current()
        self.form_panel.build(self.doc, self.current)
        self.update_thumbs([self.current])

    def close_document(self):
        if self.doc is not None:
            self.doc.close()
        self.doc = None
        self.stack.setCurrentIndex(0)
        self.tipbar.hide()
        self.modebar.hide()
        self._fill_recent()
        self._update_state()

    def maybe_save(self):
        if self.doc is not None:
            self._flush_all()
        if not self.is_dirty():
            return True
        r = ask(self, f"Le document « {self.display_name} » a été modifié.\n\n"
                      "Voulez-vous enregistrer les modifications ?",
                [("💾 Enregistrer", QMessageBox.AcceptRole), ("Ne pas enregistrer", QMessageBox.DestructiveRole),
                 ("Annuler", QMessageBox.RejectRole)])
        if r == 0:
            return self.save()
        return r == 1

    def save(self):
        if not self.doc:
            return False
        self._flush_all()
        if self.path:
            return self._save_to(self.path)
        return self.save_as()

    def save_as(self):
        if not self.doc:
            return False
        self._flush_all()
        folder = os.path.dirname(self.path) if self.path else self.settings.value("last_dir", documents_dir())
        base = os.path.splitext(self.display_name)[0]
        suggestion = os.path.join(folder, f"{base} (modifié).pdf" if self.path else f"{base}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer sous", suggestion, PDF_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        return self._save_to(path)

    def _write_pdf(self, doc, path):
        tmp = path + ".tmp"
        try:
            data = doc.tobytes(garbage=3, deflate=True)
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return True
        except PermissionError:
            warn(self, "Impossible d'enregistrer ici.",
                 detail="Le fichier est peut-être ouvert dans un autre logiciel (fermez-le), "
                        "ou le dossier est protégé. Essayez « Enregistrer sous… » dans vos Documents.")
        except Exception as e:
            warn(self, "L'enregistrement a échoué.", detail=str(e))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False

    def _save_to(self, path):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self._used_font:
                try:
                    self.doc.subset_fonts()
                except Exception:
                    pass
            ok = self._write_pdf(self.doc, path)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            return False
        self.path = path
        self.display_name = os.path.basename(path)
        self.saved_id = self.state_id
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._add_recent(path)
        self._update_state()
        self.flash(f"✅ Document enregistré : <b>{self.display_name}</b>")
        return True

    # recent
    def _recent(self):
        r = self.settings.value("recent", [])
        if isinstance(r, str):
            r = [r]
        return list(r or [])

    def _add_recent(self, path):
        r = [p for p in self._recent() if os.path.normcase(p) != os.path.normcase(path)]
        self.settings.setValue("recent", [path] + r[:7])

    def _remove_recent(self, path):
        self.settings.setValue("recent", [p for p in self._recent() if p != path])
        self._fill_recent()

    def _fill_recent(self):
        self.recent_list.clear()
        items = [p for p in self._recent() if os.path.exists(p)]
        for p in items:
            it = QListWidgetItem(f"📄  {os.path.basename(p)}")
            it.setToolTip(p)
            it.setData(Qt.UserRole, p)
            self.recent_list.addItem(it)
        self.recent_title.setVisible(bool(items))
        self.recent_list.setVisible(bool(items))

    # ----------------------------------------------------------- affichage
    def set_fit(self, mode):
        self.fit_mode = mode
        self.render_current()

    def zoom_by(self, f):
        if not self.doc:
            return
        self.fit_mode = None
        self.zoom = max(0.25, min(5.0, self.zoom * f))
        self.render_current()

    def render_current(self):
        if not self.doc:
            return
        page = self.doc[self.current]
        pw, ph = page.rect.width, page.rect.height
        vp = self.scroll.viewport()
        if self.fit_mode == "width":
            self.zoom = (vp.width() - 2 * MARGIN - 4) / (pw * PT2PX)
        elif self.fit_mode == "page":
            self.zoom = min((vp.width() - 2 * MARGIN - 4) / (pw * PT2PX),
                            (vp.height() - 2 * MARGIN - 4) / (ph * PT2PX))
        self.zoom = max(0.25, min(5.0, self.zoom))
        scale = self.zoom * PT2PX
        dpr = self.view.devicePixelRatioF() or 1.0
        hidden = find_annot(page, self._editing_xref) if self._editing_xref is not None else None
        if hidden is not None:                      # le texte en cours de modification est masqué
            old_flags = hidden.flags
            hidden.set_flags(old_flags | pymupdf.PDF_ANNOT_IS_HIDDEN)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale * dpr, scale * dpr), alpha=False)
        if hidden is not None:
            hidden.set_flags(old_flags)
        img = pix_to_qimage(pix)
        img.setDevicePixelRatio(dpr)
        rm = page.rotation_matrix
        fields = []
        for w in page.widgets() or []:
            if w.field_type == W_BUTTON:
                continue
            r = w.rect * rm
            fields.append((QRectF(r.x0, r.y0, r.width, r.height), w.xref))
        self.page_images, rects = [], []
        for inf in page.get_image_info(xrefs=True):
            r = pymupdf.Rect(inf["bbox"])
            if r.is_empty or r.is_infinite:
                continue
            v = (r * rm) & page.rect
            if v.is_empty:
                continue
            self.page_images.append(inf)
            rects.append(QRectF(v.x0, v.y0, v.width, v.height))
        self.page_objects, objs = [], []
        for a in page.annots() or []:
            meta = get_meta(self.doc, a)
            if meta and a.xref != self._editing_xref:
                v = a.rect * rm
                self.page_objects.append({"xref": a.xref, "meta": meta, "rect": v})
                objs.append(QRectF(v.x0, v.y0, v.width, v.height))
        boxes = [QRectF(b.x0, b.y0, b.width, b.height) for b in find_boxes(page)] if self.mode == "check" else []
        self.view.set_content(img, scale, pw, ph, fields, rects, objs, boxes,
                              [o["meta"].get("k") for o in self.page_objects])
        self._place_field_editor()
        self._place_editor()
        self.zoom_label.setText(f"{round(self.zoom * 100)} %")

    def go_to(self, i, force=False, bottom=False):
        if not self.doc or self._goto_guard:
            return
        i = max(0, min(self.doc.page_count - 1, i))
        if i == self.current and not force:
            return
        self._flush_all()
        self._goto_guard = True
        try:
            self.current = i
            if self.thumbs.currentRow() != i:
                self.thumbs.setCurrentRow(i)
            self.n_spin.setMaximum(self.doc.page_count)
            self.n_spin.setValue(i + 1)
            self.n_total.setText(f"sur {self.doc.page_count}")
            self.n_prev.setEnabled(i > 0)
            self.n_next.setEnabled(i < self.doc.page_count - 1)
            self.render_current()
            self.form_panel.build(self.doc, i)
            sb = self.scroll.verticalScrollBar()
            QTimer.singleShot(0, lambda: sb.setValue(sb.maximum() if bottom else 0))
        finally:
            self._goto_guard = False

    # thumbnails
    def _placeholder(self):
        pm = QPixmap(110, 150)
        pm.fill(QColor("white"))
        return QIcon(pm)

    def rebuild_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        ph = self._placeholder()
        for i in range(self.doc.page_count):
            it = QListWidgetItem(ph, f"Page {i + 1}")
            it.setData(Qt.UserRole, i)
            self.thumbs.addItem(it)
        self.thumbs.setCurrentRow(min(self.current, self.doc.page_count - 1))
        self.thumbs.blockSignals(False)
        self._thumb_queue = list(range(self.doc.page_count))
        self._thumb_timer.start()

    def update_thumbs(self, indices):
        self._thumb_queue = list(indices) + [i for i in self._thumb_queue if i not in indices]
        self._thumb_timer.start()

    def _thumb_step(self):
        if not self.doc or not self._thumb_queue:
            self._thumb_timer.stop()
            return
        dpr = self.devicePixelRatioF() or 1.0
        for _ in range(6):
            if not self._thumb_queue:
                break
            i = self._thumb_queue.pop(0)
            if i >= self.doc.page_count or i >= self.thumbs.count():
                continue
            page = self.doc[i]
            z = min(110 / page.rect.width, 150 / page.rect.height) * dpr
            img = pix_to_qimage(page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False))
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            self.thumbs.item(i).setIcon(QIcon(pm))

    def _on_thumb_row(self, row):
        if row >= 0 and not self._goto_guard:
            self.go_to(row)

    def selected_pages(self):
        rows = sorted({self.thumbs.row(it) for it in self.thumbs.selectedItems()})
        return rows or [self.current]

    # ------------------------------------------------------------ pages
    def _reorder(self, order):
        """Réordonne les pages sans perdre les formulaires (move_page)."""
        cur = list(range(self.doc.page_count))
        for t, orig in enumerate(order):
            idx = cur.index(orig)
            if idx != t:
                self.doc.move_page(idx, t)
                cur.insert(t, cur.pop(idx))

    def _after_structure_change(self, new_current, select=None):
        self.current = max(0, min(self.doc.page_count - 1, new_current))
        self.rebuild_thumbs()
        self.go_to(self.current, force=True)
        if select:
            self.thumbs.blockSignals(True)
            for r in select:
                if 0 <= r < self.thumbs.count():
                    self.thumbs.item(r).setSelected(True)
            self.thumbs.blockSignals(False)
        self._update_state()

    def rotate(self, delta):
        if not self.doc:
            return
        pages = self.selected_pages()
        self.snapshot()
        for i in pages:
            p = self.doc[i]
            p.set_rotation((p.rotation + delta) % 360)
        self.update_thumbs(pages)
        self.render_current()
        self.form_panel.build(self.doc, self.current)

    def move_pages(self, d):
        if not self.doc:
            return
        pages = self.selected_pages()
        n = self.doc.page_count
        if (d < 0 and pages[0] == 0) or (d > 0 and pages[-1] == n - 1):
            return
        order = list(range(n))
        for p in (pages if d < 0 else reversed(pages)):
            order[p], order[p + d] = order[p + d], order[p]
        self.snapshot()
        self._reorder(order)
        cur = self.current + d if self.current in pages else self.current
        self._after_structure_change(cur, [p + d for p in pages])

    def _on_thumbs_reordered(self):
        if not self.doc:
            return
        order = [self.thumbs.item(r).data(Qt.UserRole) for r in range(self.thumbs.count())]
        if order == list(range(self.doc.page_count)) or sorted(order) != list(range(self.doc.page_count)):
            return
        self.snapshot()
        self._reorder(order)
        self._after_structure_change(order.index(self.current))
        self.flash("✅ Ordre des pages modifié.")

    def delete_pages(self):
        if not self.doc:
            return
        pages = self.selected_pages()
        if len(pages) >= self.doc.page_count:
            warn(self, "Un document doit garder au moins une page.")
            return
        if len(pages) == 1:
            q = f"Supprimer la page {pages[0] + 1} ?"
        else:
            q = f"Supprimer les {len(pages)} pages sélectionnées ({', '.join(str(p + 1) for p in pages)}) ?"
        if ask(self, q, [("🗑 Supprimer", QMessageBox.DestructiveRole), ("Annuler", QMessageBox.RejectRole)]) != 0:
            return
        self.snapshot()
        self.doc.delete_pages(pages)
        self._after_structure_change(pages[0])
        self.flash("🗑 Page supprimée. (Vous pouvez « Annuler » si c'était une erreur.)")

    def add_blank_page(self):
        if not self.doc:
            return
        ref = self.doc[self.current].rect
        self.snapshot()
        self.doc.new_page(pno=self.current + 1, width=ref.width, height=ref.height)
        self._after_structure_change(self.current + 1)
        self.flash("📄 Page blanche ajoutée après la page affichée.")

    def insert_pdf(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choisir le PDF à ajouter",
                                              self.settings.value("last_dir", documents_dir()), PDF_FILTER)
        if not path:
            return
        src = self._open_any(path)
        if src is None:
            return
        self.snapshot()
        at = self.current + 1
        self.doc.insert_pdf(src, start_at=at)
        n = src.page_count
        src.close()
        self._after_structure_change(at)
        self.flash(f"📑 {n} page(s) ajoutée(s) après la page {at}.")

    def insert_image_pages(self):
        if not self.doc:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Choisir les photos", documents_dir(), IMAGE_FILTER)
        if not paths:
            return
        self.snapshot()
        at = self.current + 1
        k = 0
        for p in paths:
            img = load_image_file(p)
            if img is not None:
                add_image_as_page(self.doc, img, at + k)
                k += 1
        if not k:
            self.drop_snapshot()
            warn(self, "Aucune image n'a pu être lue.")
            return
        self._after_structure_change(at)
        self.flash(f"📷 {k} page(s) ajoutée(s).")

    def extract_pages(self):
        if not self.doc:
            return
        self._flush_all()
        pages = self.selected_pages()
        base = os.path.splitext(self.display_name)[0]
        label = f"page {pages[0] + 1}" if len(pages) == 1 else f"pages {', '.join(str(p + 1) for p in pages)}"
        folder = os.path.dirname(self.path) if self.path else documents_dir()
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer les pages sélectionnées",
                                              os.path.join(folder, f"{base} - {label}.pdf"), PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        new = pymupdf.open()
        for p in pages:
            new.insert_pdf(self.doc, from_page=p, to_page=p)
        if self._write_pdf(new, path):
            self.flash(f"📤 {len(pages)} page(s) enregistrée(s) dans <b>{os.path.basename(path)}</b>.")
        new.close()

    def combine(self, images=False):
        dlg = CombineDialog(self, images_mode=images)
        if dlg.exec() != QDialog.Accepted:
            return
        paths = dlg.list.paths()
        if not paths or not self.maybe_save():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out = pymupdf.open()
            for p in paths:
                src = self._open_any(p)
                if src is not None:
                    out.insert_pdf(src)
                    src.close()
        finally:
            QApplication.restoreOverrideCursor()
        if out.page_count == 0:
            warn(self, "Aucun fichier n'a pu être ajouté.")
            return
        name = "Photos.pdf" if images else "Document combiné.pdf"
        self.set_document(pymupdf.open("pdf", out.tobytes()), None, name, dirty=True)
        self.flash(f"📚 Document créé ({out.page_count} pages). N'oubliez pas de cliquer sur <b>Enregistrer</b>.",
                   kind="mode")

    # --------------------------------------------------------- impression
    def compress_doc(self):
        if not self.doc:
            return
        self._flush_all()
        self.set_mode(None)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self._used_font:
                try:
                    self.doc.subset_fonts()
                except Exception:
                    pass
            data = self.doc.tobytes(garbage=3, deflate=True)
        finally:
            QApplication.restoreOverrideCursor()
        size = len(data)
        dlg = CompressDialog(self, self.settings, size)
        if dlg.exec() != QDialog.Accepted:
            return
        mode, gray, target = dlg.mode(), dlg.gray.isChecked(), dlg.target_bytes()
        levels = {"light": [1], "strong": [4]}.get(mode, list(range(len(COMPRESS_LEVELS))))
        progress = QProgressDialog("Réduction de la taille en cours…", "Annuler", 0, len(levels), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        best = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for k, lv in enumerate(levels):
                progress.setValue(k)
                progress.setLabelText(f"Réduction de la taille en cours… (essai {k + 1})" if mode == "target"
                                      else "Réduction de la taille en cours…")
                QApplication.processEvents()
                if progress.wasCanceled():
                    return
                try:
                    out = compress_pdf(data, lv, gray)
                except Exception as e:
                    warn(self, "La réduction a échoué.", detail=str(e))
                    return
                if best is None or len(out) < len(best):
                    best = out
                if mode != "target" or len(out) <= target:
                    best = out
                    break
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()
        new = len(best)
        gain = max(0, round(100 * (1 - new / size))) if size else 0
        reached = mode != "target" or new <= target
        if new >= size * 0.97 and not gray:
            info(self, f"Ce document est déjà très léger ({human_size(size)}) : il n'a pas pu être réduit davantage.\n\n"
                       "Il contient surtout du texte, qui prend très peu de place.")
            return
        if reached:
            msg = f"✅ Nouvelle taille : <b>{human_size(new)}</b> au lieu de {human_size(size)} (−{gain} %)."
        else:
            msg = (f"⚠️ Impossible de descendre sous <b>{human_size(target)}</b> sans rendre le document illisible.<br>"
                   f"Le mieux obtenu : <b>{human_size(new)}</b> au lieu de {human_size(size)} (−{gain} %).<br><br>"
                   "Astuce : cochez « noir et blanc », ou enregistrez seulement les pages utiles "
                   "(« Enregistrer ces pages à part… »).")
        if ask(self, msg + "<br><br>Enregistrer cette version plus légère ?",
               [("💾 Enregistrer…", QMessageBox.AcceptRole), ("Annuler", QMessageBox.RejectRole)]) != 0:
            return
        base = os.path.splitext(self.display_name)[0]
        folder = os.path.dirname(self.path) if self.path else self.settings.value("last_dir", documents_dir())
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer la version plus légère",
                                              os.path.join(folder, f"{base} (léger).pdf"), PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if self.path and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.path)):
            if ask(self, "Remplacer le document d'origine par la version plus légère ?\n\n"
                         "La qualité d'origine sera perdue.",
                   [("Remplacer", QMessageBox.DestructiveRole), ("Annuler", QMessageBox.RejectRole)]) != 0:
                return
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(best)
            os.replace(tmp, path)
        except PermissionError:
            warn(self, "Impossible d'enregistrer ici.",
                 detail="Le fichier est peut-être ouvert dans un autre logiciel, ou le dossier est protégé.")
            return
        except Exception as e:
            warn(self, "L'enregistrement a échoué.", detail=str(e))
            return
        self.flash(f"🗜️ Version plus légère enregistrée : <b>{html.escape(os.path.basename(path))}</b> "
                   f"({human_size(new)}).")

    def print_doc(self):
        if not self.doc:
            return
        self._flush_all()
        dlg = PrintOptionsDialog(self, self.settings, self.doc.page_count, self.current)
        if dlg.exec() != QDialog.Accepted:
            return
        printer = dlg.configured_printer()
        printer.setDocName(self.display_name)
        gray = dlg.c_gray.isChecked()
        copies = dlg.copies.value()
        # si le pilote ne gère pas les copies multiples, on les imprime nous-mêmes
        manual = copies if not printer.supportsMultipleCopies() else 1
        if manual > 1:
            printer.setCopyCount(1)
        self._print_pages(printer, dlg.pages() * manual, gray)

    def _print_pages(self, printer, pages, gray=False):
        progress = QProgressDialog("Impression en cours…", "Annuler", 0, len(pages), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(300)
        painter = QPainter()
        if not painter.begin(printer):
            warn(self, "Impossible de démarrer l'impression.",
                 detail="Vérifiez que l'imprimante est allumée et branchée, puis réessayez.")
            return False
        dpi = min(printer.resolution(), 300)
        cs = pymupdf.csGRAY if gray else pymupdf.csRGB
        for k, i in enumerate(pages):
            if progress.wasCanceled():
                break
            progress.setValue(k)
            QApplication.processEvents()
            if k:
                printer.newPage()
            page = self.doc[i]
            z = dpi / 72
            img = pix_to_qimage(page.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=cs, alpha=False))
            area = painter.viewport()
            if (img.width() > img.height()) != (area.width() > area.height()):
                img = img.transformed(QTransform().rotate(90))
            sc = min(area.width() / img.width(), area.height() / img.height())
            w, h = img.width() * sc, img.height() * sc
            painter.drawImage(QRectF((area.width() - w) / 2, (area.height() - h) / 2, w, h), img)
        painter.end()
        progress.setValue(len(pages))
        self.flash("🖨️ Document envoyé à l'imprimante" + (" (noir et blanc)." if gray else "."))
        return True

    # --------------------------------------------------- modes d'édition
    def set_mode(self, mode, message=None, extra=None):
        self._commit_field_editor()
        if self._editor is not None:
            self._commit_editor()
        was_check = self.mode == "check"
        self.mode = mode
        if was_check and mode != "check":
            QTimer.singleShot(0, self.render_current)
        self.view.mode = mode
        self.view.update()
        if mode is None:
            if self.modebar.cancel_btn.isVisible():
                self.modebar.hide()
            self.view.setCursor(Qt.ArrowCursor)
        else:
            self.modebar.show_message(message, kind="mode", cancel=True, extra=extra)

    def sign(self):
        if not self.doc:
            return
        self._flush_all()
        dlg = SignatureDialog(self)
        if dlg.exec() != QDialog.Accepted or dlg.result_image is None:
            return
        self._start_place_image(dlg.result_image, 0.28,
                                "✍️ <b>Cliquez sur le document</b> à l'endroit où placer votre signature.<br>"
                                "Tournez la molette de la souris pour l'agrandir ou la réduire.")

    def add_image(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choisir une image", documents_dir(), IMAGE_FILTER)
        if not path:
            return
        img = load_image_file(path)
        if img is None:
            warn(self, "Impossible de lire cette image.")
            return
        self._start_place_image(img, 0.4,
                                "🖼️ <b>Cliquez sur le document</b> à l'endroit où placer l'image.<br>"
                                "Tournez la molette de la souris pour l'agrandir ou la réduire.")

    def _start_place_image(self, img, width_frac, message):
        self._ghost_bytes = image_bytes_for_pdf(img)
        self.view.ghost_img = img
        self.view.ghost_aspect = img.width() / max(1, img.height())
        pw = self.doc[self.current].rect.width
        self.view.ghost_w = pw * width_frac
        self.set_mode("place_image", message)

    # ---------------------------------------------------- texte libre
    def _type_tools(self):
        if hasattr(self, "_type_tools_w"):
            return self._type_tools_w
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Taille :"))
        self.type_size = QSpinBox()
        self.type_size.setRange(6, 48)
        self.type_size.setValue(int(self.settings.value("text_size", 11)))
        lay.addWidget(self.type_size)
        self.type_color = QComboBox()
        for name, c in (("Noir", "#000000"), ("Bleu", "#1d3fa8"), ("Rouge", "#c81e1e")):
            self.type_color.addItem(name, c)
        self.type_color.setCurrentIndex(max(0, self.type_color.findData(self.settings.value("text_color", "#000000"))))
        lay.addWidget(self.type_color)
        self.type_fmt = {}
        for key, label, tip, css in (("b", "G", "Gras", "font-weight:700;"),
                                     ("i", "I", "Italique", "font-style:italic;"),
                                     ("u", "S", "Souligné", "text-decoration:underline;")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedWidth(40)
            b.setStyleSheet(f"QPushButton {{ {css} }} QPushButton:checked {{ background:#bfdbfe; border-color:#2563eb; }}")
            b.setChecked(self.settings.value(f"text_{key}", False, type=bool))
            b.toggled.connect(self._on_type_style)
            self.type_fmt[key] = b
            lay.addWidget(b)
        self.type_size.valueChanged.connect(self._on_type_style)
        self.type_color.currentIndexChanged.connect(self._on_type_style)
        date = QPushButton("📅 Date du jour")
        date.setToolTip("Écrit la date d'aujourd'hui dans la zone de texte en cours")
        date.clicked.connect(self._insert_date)
        lay.addWidget(date)
        self.type_delete = QPushButton("🗑 Supprimer ce texte")
        self.type_delete.clicked.connect(self._delete_edited_text)
        self.type_delete.hide()
        lay.addWidget(self.type_delete)
        self._type_tools_w = w
        return w

    def _type_meta(self, text):
        return {"k": "text", "t": text, "fs": self.type_size.value(), "c": self.type_color.currentData(),
                "b": self.type_fmt["b"].isChecked(), "i": self.type_fmt["i"].isChecked(),
                "u": self.type_fmt["u"].isChecked()}

    def _set_type_tools(self, meta):
        self._type_tools()
        widgets = [self.type_size, self.type_color] + list(self.type_fmt.values())
        for w in widgets:
            w.blockSignals(True)
        self.type_size.setValue(int(meta.get("fs", 11)))
        self.type_color.setCurrentIndex(max(0, self.type_color.findData(meta.get("c", "#000000"))))
        for k, b in self.type_fmt.items():
            b.setChecked(bool(meta.get(k)))
        for w in widgets:
            w.blockSignals(False)

    def _on_type_style(self, *a):
        self.settings.setValue("text_size", self.type_size.value())
        self.settings.setValue("text_color", self.type_color.currentData())
        for k, b in self.type_fmt.items():
            self.settings.setValue(f"text_{k}", b.isChecked())
        self._place_editor()
        if self._editor is not None:
            self._editor.setFocus()

    def _insert_date(self):
        today = datetime.date.today().strftime("%d/%m/%Y")
        if self._editor is not None:
            self._editor.insertPlainText(today)
            self._editor.setFocus()
        else:
            self.modebar.label.setText("📅 <b>Cliquez d'abord sur le document</b> à l'endroit où écrire la date, "
                                       "puis cliquez sur « Date du jour ».")

    def _type_message(self):
        return ("🔤 <b>Cliquez sur la page</b> pour écrire, <b>Entrée</b> pour valider.<br>"
                "Cliquez sur un texte pour le corriger, glissez-le pour le déplacer.")

    def add_text(self):
        if not self.doc:
            return
        self._flush_all()
        self.set_mode("type", self._type_message(), extra=self._type_tools())

    def on_type_at(self, pt):
        self._commit_editor()
        fs = self.type_size.value()
        self._open_editor(QPointF(pt.x(), pt.y() - TEXT_LH * fs / 2), "", None)

    def _open_editor(self, anchor, text, xref):
        ed = InlineEdit(self.view)
        ed.setPlainText(text)
        ed.commitRequested.connect(self._commit_editor)
        ed.textChanged.connect(self._place_editor)
        self._editor, self._editor_pt, self._editing_xref = ed, anchor, xref
        self.type_delete.setVisible(xref is not None)
        if xref is not None:
            self.render_current()                   # masque l'original pendant la modification
            self.modebar.label.setText("✏️ <b>Vous modifiez ce texte.</b> Changez-le, puis <b>Entrée</b> pour valider.<br>"
                                       "Vous pouvez aussi changer sa taille, sa couleur, ou le supprimer.")
        else:
            self.modebar.label.setText(self._type_message())
        self._place_editor()
        ed.show()
        ed.setFocus()
        ed.moveCursor(ed.textCursor().MoveOperation.End)

    def _place_editor(self):
        ed = self._editor
        if ed is None or not self.doc:
            return
        fs, s = self.type_size.value(), self.view.scale
        f = QFont("Arial")
        f.setPixelSize(max(6, int(fs * s)))
        f.setBold(self.type_fmt["b"].isChecked())
        f.setItalic(self.type_fmt["i"].isChecked())
        f.setUnderline(self.type_fmt["u"].isChecked())
        ed.setFont(f)
        ed.setStyleSheet(f"QPlainTextEdit {{ background: #fffbe0; border: 2px solid #2563eb;"
                         f" border-radius: 3px; padding: 0px; color: {self.type_color.currentData()}; }}")
        fm = ed.fontMetrics()
        lines = ed.toPlainText().split("\n")
        text_w = max(fm.horizontalAdvance(l) for l in lines) + fm.averageCharWidth() * 3
        page_right = int(MARGIN + self.doc[self.current].rect.width * s)
        x = int(MARGIN + self._editor_pt.x() * s) - 2
        y = int(MARGIN + self._editor_pt.y() * s)
        w = max(120, min(max(text_w, 120), page_right - x + 40)) + 4
        h = int(len(lines) * max(fm.lineSpacing(), TEXT_LH * fs * s)) + 8
        ed.setGeometry(x, y, int(w), h)

    def _close_editor(self):
        ed = self._editor
        self._editor = None
        xref, self._editing_xref = self._editing_xref, None
        if hasattr(self, "type_delete"):
            self.type_delete.hide()
        text = ed.toPlainText().rstrip() if ed else ""
        if ed:
            ed.hide()
            ed.deleteLater()
        return text, xref

    def _commit_editor(self):
        if self._editor is None:
            return
        anchor = self._editor_pt
        text, xref = self._close_editor()
        if not self.doc:
            return
        page = self.doc[self.current]
        meta = self._type_meta(text)
        old = find_annot(page, xref) if xref is not None else None
        if old is not None and get_meta(self.doc, old) == meta:
            self.render_current()                    # rien n'a changé : on réaffiche l'original
            return
        if not text.strip() and old is None:
            return
        self.snapshot()
        page = self.doc[self.current]
        if old is not None:
            page.delete_annot(find_annot(page, xref))
        if text.strip():
            try:
                create_text_annot(self.doc, page, anchor.x(), anchor.y(), meta)
            except Exception as e:
                warn(self, "Impossible d'ajouter ce texte.", detail=str(e))
        self.render_current()
        self.update_thumbs([self.current])

    def _delete_edited_text(self):
        if self._editor is not None:
            self._editor.setPlainText("")
            self._commit_editor()
            self.flash("🗑 Texte supprimé.") if self.mode is None else None

    def edit_text_object(self, obj):
        self._commit_editor()
        if self.mode != "type":
            self.set_mode("type", self._type_message(), extra=self._type_tools())
        self._set_type_tools(obj["meta"])
        r = obj["rect"]
        self._open_editor(QPointF(r.x0, r.y0), obj["meta"].get("t", ""), obj["xref"])

    # ---------------------------------------------------- coches
    def _check_tools(self):
        if hasattr(self, "_check_tools_w"):
            return self._check_tools_w
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Type :"))
        self.check_kind = QComboBox()
        for label, m in CHECK_MARKS:
            self.check_kind.addItem(label, m)
        self.check_kind.setCurrentIndex(max(0, self.check_kind.findData(self.settings.value("check_mark", "x"))))
        self.check_kind.currentIndexChanged.connect(
            lambda i: self.settings.setValue("check_mark", self.check_kind.itemData(i)))
        lay.addWidget(self.check_kind)
        self._check_tools_w = w
        return w

    def check_mode(self):
        if not self.doc:
            return
        self._flush_all()
        self.set_mode("check", "✔️ <b>Cliquez sur une case</b> pour la cocher (les cases trouvées sont en vert). "
                               "Cliquez sur une coche pour l'enlever.<br>"
                               "Vous pouvez aussi cliquer n'importe où sur la page pour y mettre une coche.",
                      extra=self._check_tools())
        self.render_current()

    def on_check_at(self, pt, box):
        if not self.doc:
            return
        page = self.doc[self.current]
        p = pymupdf.Point(pt.x(), pt.y())
        for w in page.widgets() or []:          # vraie case de formulaire : on la coche directement
            if w.field_type in (W_CHECK, W_RADIO) and (w.rect * page.rotation_matrix).contains(p):
                if w.field_type == W_CHECK:
                    self.on_field_changed(self.current, w.xref, w.field_value in (False, None, "", "Off"), "check")
                else:
                    self.on_field_changed(self.current, w.xref, True, "radio")
                self.form_panel.build(self.doc, self.current)
                return
        if box.isEmpty():
            size, cx, cy = 11.0, pt.x(), pt.y()
        else:
            size, cx, cy = min(box.width(), box.height()), box.center().x(), box.center().y()
        self.snapshot()
        create_check_annot(self.doc, page, cx, cy, size, self.check_kind.currentData())
        self.render_current()
        self.update_thumbs([self.current])

    # ---------------------------------------------------- objets ajoutés
    def _recreate(self, obj, dx=0.0, dy=0.0):
        page = self.doc[self.current]
        a = find_annot(page, obj["xref"])
        if a is None:
            return
        page.delete_annot(a)
        meta, r = obj["meta"], obj["rect"]
        if meta.get("k") == "text":
            create_text_annot(self.doc, page, r.x0 + dx, r.y0 + dy, meta)
        else:
            c = (r.tl + r.br) / 2
            create_check_annot(self.doc, page, c.x + dx, c.y + dy, meta.get("s", 11), meta.get("m", "x"))

    def on_object_clicked(self, i):
        if i >= len(self.page_objects):
            return
        obj = self.page_objects[i]
        if obj["meta"].get("k") == "text":
            self.edit_text_object(obj)
        elif self.mode == "check":
            self.delete_object(obj)
        else:
            self.flash("✔️ Faites glisser la coche pour la déplacer, ou faites un clic droit pour la supprimer.",
                       kind="mode")

    def on_object_moved(self, i, dx, dy):
        if i >= len(self.page_objects):
            return
        self._commit_editor()
        obj = self.page_objects[i]
        self.snapshot()
        self._recreate(obj, dx, dy)
        self.render_current()
        self.update_thumbs([self.current])

    def on_object_menu(self, i, pos):
        if i >= len(self.page_objects):
            return
        obj = self.page_objects[i]
        m = QMenu(self)
        if obj["meta"].get("k") == "text":
            m.addAction("✏️  Modifier le texte", lambda: self.edit_text_object(obj))
            m.addAction("🗑  Supprimer ce texte", lambda: self.delete_object(obj))
        else:
            m.addAction("🗑  Supprimer la coche", lambda: self.delete_object(obj))
        m.exec(pos)

    def delete_object(self, obj):
        self._commit_editor()
        page = self.doc[self.current]
        a = find_annot(page, obj["xref"])
        if a is None:
            return
        self.snapshot()
        page.delete_annot(find_annot(self.doc[self.current], obj["xref"]))
        self.render_current()
        self.update_thumbs([self.current])
        if self.mode is None:
            self.flash("🗑 Supprimé. (« Annuler » pour revenir en arrière.)")

    def on_placed(self, vr):
        if not self.doc:
            return
        page = self.doc[self.current]
        rect = pymupdf.Rect(vr.x(), vr.y(), vr.right(), vr.bottom())
        self.snapshot()
        try:
            if self.mode == "place_image":
                page.insert_image(rect * page.derotation_matrix, stream=self._ghost_bytes,
                                  rotate=page.rotation, keep_proportion=True)
                msg = "✅ Ajouté ! S'il n'est pas bien placé, cliquez sur « Annuler » et recommencez."
            else:
                self.drop_snapshot()
                return
        except Exception as e:
            self.drop_snapshot()
            warn(self, "Impossible d'ajouter cet élément.", detail=str(e))
            return
        self.set_mode(None)
        self.render_current()
        self.update_thumbs([self.current])
        self.flash(msg)

    def remove_image_mode(self):
        if not self.doc:
            return
        self._flush_all()
        self.set_mode("remove_image", self._remove_msg())

    def _remove_msg(self):
        if not self.page_images:
            return ("✂️ <b>Aucune image détectée sur cette page.</b> Changez de page, ou appuyez sur Échap.<br>"
                    "<small>Seules les photos et images peuvent être retirées (pas le texte ni les dessins).</small>")
        return ("✂️ <b>Cliquez sur l'image à retirer</b> (elle devient rouge quand la souris passe dessus).<br>"
                "Vous pouvez en retirer plusieurs. Cliquez sur « Terminer » quand vous avez fini.")

    def on_image_clicked(self, idx):
        if not self.doc or idx >= len(self.page_images):
            return
        page = self.doc[self.current]
        target = self.page_images[idx]
        t_rect = pymupdf.Rect(target["bbox"])
        others = [pymupdf.Rect(i["bbox"]) for k, i in enumerate(self.page_images) if k != idx]

        def signature():
            return sorted((i.get("xref", 0), tuple(round(v, 1) for v in i["bbox"]))
                          for i in page.get_image_info(xrefs=True))
        before = signature()
        self.snapshot()
        # 1) petite zone de masquage au-dessus de cette image seulement
        point = None
        for fy in (0.5, 0.3, 0.7, 0.15, 0.85):
            for fx in (0.5, 0.3, 0.7, 0.15, 0.85):
                pt = pymupdf.Point(t_rect.x0 + t_rect.width * fx, t_rect.y0 + t_rect.height * fy)
                if not any(o.contains(pt) for o in others):
                    point = pt
                    break
            if point:
                break
        if point is not None:
            page.add_redact_annot(pymupdf.Rect(point.x - 0.5, point.y - 0.5, point.x + 0.5, point.y + 0.5))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_NONE)
        # 2) sinon : remplacer l'image par une image vide
        if signature() == before and target.get("xref"):
            try:
                page.delete_image(target["xref"])
            except Exception:
                pass
        if signature() == before and not point:
            self.drop_snapshot()
            warn(self, "Cette image n'a pas pu être retirée.")
            return
        self.render_current()
        self.update_thumbs([self.current])
        self.modebar.show_message("✅ Image retirée. " + self._remove_msg(), kind="mode", cancel=True)

    # ---------------------------------------------------------- formulaire
    def toggle_form(self):
        self.form_panel.setVisible(self.b_form.isChecked())

    # ------------------------------------------- saisie directement sur la page
    def _widget(self, page, xref):
        return next((w for w in page.widgets() or [] if w.xref == xref), None)

    def _refresh_panel(self):
        if self.form_panel.isVisible():
            self.form_panel.build(self.doc, self.current)

    def on_field_clicked(self, xref):
        self._commit_field_editor()
        page = self.doc[self.current]
        w = self._widget(page, xref)
        if w is None:
            return
        if w.field_type == W_CHECK:
            self.on_field_changed(self.current, xref, w.field_value in (False, None, "", "Off"), "check")
            self._refresh_panel()
        elif w.field_type == W_RADIO:
            self.on_field_changed(self.current, xref, True, "radio")
            self._refresh_panel()
        elif w.field_type in (W_TEXT, W_COMBO, W_LIST):
            self._open_field_editor(w)
        elif w.field_type == W_SIGN:
            self.flash("✍️ Pour signer ici, cliquez sur « Signer » en haut, puis sur cette zone.", kind="mode")

    def _open_field_editor(self, w):
        page = self.doc[self.current]
        r = w.rect * page.rotation_matrix
        readonly = bool(w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY)
        if readonly:
            self.flash("🔒 Cette case ne peut pas être modifiée.", kind="mode")
            return
        label = w.field_label or pretty_field_name(w.field_name)
        n = 0
        if w.field_type == W_TEXT:
            if w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                ed = FieldMulti(self.view)
                ed.setPlainText(w.field_value or "")
            else:
                ed = FieldLine(self.view)
                ed.setText(w.field_value or "")
                if w.field_flags & pymupdf.PDF_TX_FIELD_IS_COMB and w.text_maxlen > 0:
                    n = w.text_maxlen
                    ed.setMaxLength(n + 4)
                    ed.setPlaceholderText(comb_placeholder(label, n))
                elif w.text_maxlen and w.text_maxlen > 0:
                    ed.setMaxLength(w.text_maxlen)
            kind = "text"
        else:
            ed = FieldCombo(self.view)
            ed.addItem("— choisir —", "")
            for c in (w.choice_values or []):
                if isinstance(c, (list, tuple)):
                    ed.addItem(str(c[1] if len(c) > 1 else c[0]), str(c[0]))
                else:
                    ed.addItem(str(c), str(c))
            ed.setCurrentIndex(max(0, ed.findData(w.field_value if isinstance(w.field_value, str) else "")))
            ed.activated.connect(lambda *_: self._commit_field_editor(1))
            kind = "choice"
        ed.setToolTip(label)
        ed.navigate.connect(lambda d, e=ed: self._field_ed is e and self._commit_field_editor(d))
        self._field_ed = ed
        self._field_info = {"xref": w.xref, "page": self.current, "rect": QRectF(r.x0, r.y0, r.width, r.height),
                            "kind": kind, "n": n, "fs": w.text_fontsize or 0}
        self._place_field_editor()
        ed.show()
        ed.setFocus()
        if isinstance(ed, FieldLine):
            ed.end(False)
        self.scroll.ensureWidgetVisible(ed, 60, 120)
        if isinstance(ed, FieldCombo):
            QTimer.singleShot(0, ed.showPopup)
        if not self._field_hint_done:
            self._field_hint_done = True
            self.modebar.show_message(f"✏️ <b>{html.escape(label)}</b> — tapez votre réponse. "
                                      "<b>Entrée</b> ou <b>Tab</b> : case suivante · <b>Maj+Tab</b> : case précédente.",
                                      kind="mode", cancel=False, timeout=12000)

    def _place_field_editor(self):
        ed = self._field_ed
        if ed is None or self._field_info["page"] != self.current:
            return
        s = self.view.scale
        r = self._field_info["rect"]
        fs = self._field_info["fs"] or min(12.0, r.height() * 0.7)
        f = QFont("Arial")
        f.setPixelSize(max(13, int(fs * s)))
        ed.setFont(f)
        ed.setStyleSheet("background: #ffffff; border: 2px solid #2563eb; border-radius: 3px; padding: 0 3px;")
        h = max(int(r.height() * s) + 4, QFontMetricsF(f).height() + 10)
        w = max(int(r.width() * s) + 4, 70)
        x = int(MARGIN + r.x() * s) - 2
        y = int(MARGIN + r.y() * s + r.height() * s / 2 - h / 2)
        ed.setGeometry(x, y, int(w), int(h))

    def _ordered_fields(self):
        out = []
        for pi in range(self.doc.page_count):
            page = self.doc[pi]
            ws = [w for w in page.widgets() or [] if w.field_type in (W_TEXT, W_COMBO, W_LIST)
                  and not (w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY)]
            ws.sort(key=lambda w: widget_sort_key(page, w))
            out += [(pi, w.xref) for w in ws]
        return out

    def _commit_field_editor(self, nav=0):
        ed = self._field_ed
        if ed is None:
            return False
        self._field_ed = None
        inf = self._field_info
        value = ed.value() or ""
        ed.hide()
        ed.deleteLater()
        if not self.doc or inf["page"] >= self.doc.page_count:
            return True
        if inf["n"]:
            value = re.sub(r"[\s/.\-]", "", value)[:inf["n"]]
        w = self._widget(self.doc[inf["page"]], inf["xref"])
        if w is not None and (w.field_value or "") != value:
            self.on_field_changed(inf["page"], inf["xref"], value, inf["kind"])
            self._refresh_panel()
        else:
            self.view.update()
        if nav:
            order = self._ordered_fields()
            key = (inf["page"], inf["xref"])
            if key in order:
                i = order.index(key) + nav
                if 0 <= i < len(order):
                    pi, x = order[i]
                    if pi != self.current:
                        self.go_to(pi)
                    w2 = self._widget(self.doc[pi], x)
                    if w2 is not None:
                        self._open_field_editor(w2)
                elif nav > 0:
                    self.flash("✅ C'était la dernière case du document. N'oubliez pas d'<b>Enregistrer</b>.")
        return True

    def on_field_changed(self, pi, xref, value, kind):
        if not self.doc or pi >= self.doc.page_count:
            return
        key = (pi, xref)
        if self._form_key != key or kind != "text":
            self.snapshot()
            self._form_key = key
        page = self.doc[pi]
        try:
            if kind == "radio":
                target = next((w for w in page.widgets() if w.xref == xref), None)
                if target is None:
                    return
                name = target.field_name
                for w in page.widgets():
                    if w.field_type == W_RADIO and w.field_name == name:
                        w.field_value = (w.xref == xref)
                        w.update()
            else:
                for w in page.widgets():
                    if w.xref == xref:
                        if kind == "check":
                            w.field_value = bool(value)
                            w.update()
                        elif value in (None, ""):
                            # PyMuPDF ignore une valeur vide : on vide la case directement
                            self.doc.xref_set_key(xref, "V", "()")
                            w2 = self._widget(page, xref)
                            if w2 is not None:
                                w2.update()
                        else:
                            w.field_value = value
                            w.update()
                        break
        except Exception as e:
            warn(self, "Impossible de remplir cette case.", detail=str(e))
        if pi == self.current:
            self.render_current()
        self.update_thumbs([pi])
        self._update_state()

    # ---------------------------------------------------------- aide
    def maybe_show_tour(self):
        if not self.settings.value("tour_seen", False, type=bool) or \
                self.settings.value("tour_always", False, type=bool):
            self.show_tour()

    def show_tour(self):
        dlg = TourDialog(self, self.settings.value("tour_always", False, type=bool))
        dlg.exec()
        self.settings.setValue("tour_seen", True)
        self.settings.setValue("tour_always", dlg.again.isChecked())

    def enable_tips(self):
        self.settings.setValue("tips", True)
        self.tipbar.show_tip(int(self.settings.value("tip_index", 0)))

    def disable_tips(self):
        self.settings.setValue("tips", False)
        self.flash("Les astuces ne s'afficheront plus. Vous pouvez les réactiver dans « Aide ».", kind="mode")

    def about(self):
        info(self, f"<b>{APP_NAME}</b> — version {APP_VERSION}<br><br>"
                   "Lire, remplir, signer et modifier vos PDF, simplement.<br><br>"
                   f"<a href='{APP_URL}'>{APP_URL}</a><br>"
                   f"<a href='{APP_URL}/releases/latest'>Télécharger la dernière version</a><br><br>"
                   "<small>Logiciel libre (licence AGPL-3.0), basé sur PyMuPDF et Qt.</small>", "À propos")

    # ---------------------------------------------------------- événements
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith((".pdf",) + IMAGE_EXT)]
        if not paths:
            return
        if self.doc is None and len(paths) == 1:
            self.open_path(paths[0])
            return
        if self.doc is None:
            r = 0
        else:
            r = ask(self, "Que voulez-vous faire avec ce(s) fichier(s) ?",
                    [("📂 Ouvrir à la place", QMessageBox.AcceptRole),
                     ("➕ Ajouter à la fin du document", QMessageBox.AcceptRole),
                     ("Annuler", QMessageBox.RejectRole)])
        if r == 0:
            if len(paths) == 1:
                self.open_path(paths[0])
            else:
                if not self.maybe_save():
                    return
                out = pymupdf.open()
                for p in paths:
                    src = self._open_any(p)
                    if src is not None:
                        out.insert_pdf(src)
                if out.page_count:
                    self.set_document(out, None, "Document combiné.pdf", dirty=True)
        elif r == 1:
            self.snapshot()
            at = self.doc.page_count
            for p in paths:
                src = self._open_any(p)
                if src is not None:
                    self.doc.insert_pdf(src)
            if self.doc.page_count == at:
                self.drop_snapshot()
                return
            self._after_structure_change(at)
            self.flash("➕ Pages ajoutées à la fin du document.")

    def closeEvent(self, e):
        if self.maybe_save():
            e.accept()
        else:
            e.ignore()


# ----------------------------------------------------------------------------
def install_excepthook():
    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            with open(os.path.join(data_dir(), "erreurs.log"), "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.datetime.now()} ---\n{text}")
        except Exception:
            pass
        sys.__stderr__ and sys.__stderr__.write(text)
        app = QApplication.instance()
        if app:
            warn(app.activeWindow(), "Une erreur inattendue s'est produite.",
                 detail="Vous pouvez continuer. Si le problème revient, utilisez « Annuler » "
                        f"puis enregistrez votre travail.\n\n{value}")
    sys.excepthook = hook


def main():
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)
    tr = QTranslator()
    if tr.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    app.setStyleSheet(STYLE)
    install_excepthook()
    initial = next((a for a in sys.argv[1:] if os.path.isfile(a)), None)
    win = MainWindow(initial)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
