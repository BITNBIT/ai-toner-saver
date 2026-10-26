"""Motore di AI Toner Saver 1.7: lettura, scontorno, pulizia e impaginazione A4."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageSequence

try:
    import pymupdf as fitz
except ImportError:  # versioni vecchie di PyMuPDF
    import fitz

SUPPORTED = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp', '.gif', '.jfif'}
MAX_PHOTO_SIDE = 6000          # oltre non serve alla stampa e rallenta tutto
DETECT_SIDE = 1000             # lato massimo usato per lo scontorno automatico
AUTO_CONFIDENCE = 0.55         # sotto questa soglia lo scontorno viene solo proposto
CARD_RATIO = 85.6 / 53.98      # tessera, carta d'identita', patente, tessera sanitaria
CARD_W_MM, CARD_H_MM = 85.6, 53.98

A4_W_PT = 595.2756
A4_H_PT = 841.8898
MM = 72 / 25.4


@dataclass(frozen=True)
class Settings:
    mode: str = 'automatico'   # automatico | delicato | massimo | originale
    strength: int = 65
    straighten: bool = True
    remove_background: bool = True
    dpi: int = 250
    crop_document: bool = True
    clean_outer_edges: bool = True
    lighten_solids: bool = True


@dataclass
class SourcePage:
    """Una pagina appena letta, non ancora elaborata."""
    gray: np.ndarray
    color_small: np.ndarray | None
    is_photo: bool
    source: str
    number: int


@dataclass
class Detection:
    corners: np.ndarray | None     # 4x2 in pixel sorgente: alto-sx, alto-dx, basso-dx, basso-sx
    confidence: float
    method: str
    looks_scanned: bool


@dataclass
class PageResult:
    image: np.ndarray
    crop: str                       # auto | manuale | verifica | intera | digitale | spento
    crop_text: str
    corners: np.ndarray | None      # angoli usati o proposti, per l'editor
    confidence: float
    blank: bool
    card: bool
    kind: str                       # testo | misto | foto
    before: float
    after: float
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- lettura

def natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', str(text))]


def collect_files(paths) -> list[str]:
    """Espande le cartelle, filtra i formati supportati e toglie i doppioni."""
    found, seen = [], set()
    for raw in paths:
        p = Path(str(raw).strip().strip('"'))
        if p.is_dir():
            items = sorted((f for f in p.iterdir() if f.is_file()), key=lambda f: natural_key(f.name))
        elif p.is_file():
            items = [p]
        else:
            continue
        for f in items:
            if f.suffix.lower() not in SUPPORTED or f.name.lower().endswith('_pulito.pdf'):
                continue
            key = str(f.resolve()).lower()
            if key not in seen:
                seen.add(key)
                found.append(str(f.resolve()))
    return found


def _small_rgb(rgb: np.ndarray) -> np.ndarray:
    f = min(1., DETECT_SIDE / max(rgb.shape[:2]))
    if f < 1:
        rgb = cv2.resize(rgb, (max(1, round(rgb.shape[1]*f)), max(1, round(rgb.shape[0]*f))),
                         interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(rgb)


def iter_pages(path: str, dpi: int = 250):
    """Restituisce le pagine una alla volta (PDF multipagina, TIFF multipagina, foto)."""
    path = str(path)
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError('Formato non supportato: ' + path)
    if ext == '.pdf':
        document = fitz.open(path)
        try:
            if document.needs_pass:
                raise ValueError('PDF protetto da password: sbloccalo prima di caricarlo.')
            for number, page in enumerate(document, 1):
                pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width*3]
                rgb = rgb.reshape(pix.height, pix.width, 3).copy()
                # Pagina senza testo = scansione o foto dentro un PDF: si prova a scontornarla.
                has_text = bool(page.get_text('text').strip())
                yield SourcePage(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), _small_rgb(rgb), not has_text, path, number)
        finally:
            document.close()
        return
    with Image.open(path) as img:
        frames = [ImageOps.exif_transpose(frame.copy()) for frame in ImageSequence.Iterator(img)] \
            if ext in ('.tif', '.tiff') else [ImageOps.exif_transpose(img)]
        for number, frame in enumerate(frames, 1):
            if frame.mode in ('RGBA', 'LA', 'P'):
                frame = frame.convert('RGBA')
                white = Image.new('RGBA', frame.size, (255, 255, 255, 255))
                frame = Image.alpha_composite(white, frame)
            rgb = np.array(frame.convert('RGB'))
            f = min(1., MAX_PHOTO_SIDE / max(rgb.shape[:2]))
            if f < 1:
                rgb = cv2.resize(rgb, (round(rgb.shape[1]*f), round(rgb.shape[0]*f)), interpolation=cv2.INTER_AREA)
            yield SourcePage(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), _small_rgb(rgb), True, path, number)


def load_pages(path: str, dpi: int = 250):
    """Compatibilita' con la 1.6: lista di (grigio, dimensione)."""
    return [(p.gray, (A4_W_PT, A4_H_PT)) for p in iter_pages(path, dpi)]


# --------------------------------------------------------------------------- scontorno

def _ordered_corners(quad):
    """Ordina i vertici: alto-sx, alto-dx, basso-dx, basso-sx."""
    quad = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    center = quad.mean(axis=0)
    angles = np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0])
    quad = quad[np.argsort(angles)]           # senso orario partendo da sinistra-alto circa
    start = int(np.argmin(quad.sum(axis=1)))
    return np.roll(quad, -start, axis=0).astype(np.float32)


def _fit_quad(contour):
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    for eps in (.01, .015, .02, .03, .04, .055):
        approx = cv2.approxPolyDP(hull, eps * perimeter, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype(np.float32), 'poligono'
        if len(approx) < 4:
            break
    # Angoli arrotondati (tessere) o un vertice coperto: quadrilatero che avvolge la sagoma.
    if hasattr(cv2, 'approxPolyN'):
        try:
            quad = cv2.approxPolyN(hull.astype(np.float32), 4, ensure_convex=True)
            if quad is not None and quad.reshape(-1, 2).shape[0] == 4:
                return quad.reshape(4, 2).astype(np.float32), 'avvolgente'
        except cv2.error:
            pass
    return cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32), 'rettangolo'


def _interior_angles(q):
    out = []
    for i in range(4):
        a, b, c = q[i-1], q[i], q[(i+1) % 4]
        v1, v2 = a - b, c - b
        cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6))
        out.append(np.degrees(np.arccos(np.clip(cos, -1, 1))))
    return np.array(out)


def _candidate_masks(gray, rgb):
    """Diverse ipotesi di 'foglio': ognuna funziona meglio in condizioni diverse."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    masks = []
    t, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(('luminosita', blur > t))
    masks.append(('luminosita+', blur > min(250, t + 18)))
    # Differenza dal colore del piano su cui e' appoggiato (stimato dal bordo della foto).
    b = max(3, int(min(h, w) * .03))
    src = cv2.GaussianBlur(rgb, (7, 7), 0) if rgb is not None else blur[..., None]
    lab = cv2.cvtColor(src, cv2.COLOR_RGB2LAB).astype(np.float32) if rgb is not None else src.astype(np.float32)
    ring = np.concatenate([lab[:b].reshape(-1, lab.shape[2]), lab[-b:].reshape(-1, lab.shape[2]),
                           lab[:, :b].reshape(-1, lab.shape[2]), lab[:, -b:].reshape(-1, lab.shape[2])])
    background = np.median(ring, axis=0)
    dist = np.linalg.norm(lab - background, axis=2)
    dist8 = np.uint8(np.clip(dist * 255. / max(np.percentile(dist, 99.5), 1.), 0, 255))
    t, _ = cv2.threshold(dist8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(('sfondo', dist8 > max(t, 25)))
    if rgb is not None:
        hsv = cv2.cvtColor(src, cv2.COLOR_RGB2HSV).astype(np.float32)
        white = np.uint8(np.clip(hsv[..., 2] - 1.4 * hsv[..., 1], 0, 255))
        t, _ = cv2.threshold(white, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        masks.append(('carta bianca', white > t))
    # Contorni netti: funziona anche se foglio e piano hanno luminosita' simile.
    for name, low, high in (('bordi', 30, 90), ('bordi deboli', 12, 36)):
        edges = cv2.Canny(blur, low, high)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
        filled = np.zeros_like(edges)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
        masks.append((name, filled > 0))
    return masks, background


def _side_consistency(q, smooth, margin):
    h, w = smooth.shape[:2]
    d = max(3., .006 * min(h, w))
    center = q.mean(axis=0)
    out = []
    for i in range(4):
        a, c = q[i], q[(i+1) % 4]
        direction = (c - a) / (np.linalg.norm(c - a) + 1e-6)
        normal = np.array([direction[1], -direction[0]], np.float32)
        if np.dot(normal, (a + c) / 2 - center) < 0:
            normal = -normal
        n = max(30, int(np.linalg.norm(c - a) / 2))
        t = np.linspace(.05, .95, n)[:, None]
        pts = a + (c - a) * t
        inside, outside = pts - normal * d, pts + normal * d
        valid = (outside[:, 0] >= 0) & (outside[:, 0] <= w-1) & (outside[:, 1] >= 0) & (outside[:, 1] <= h-1)
        on_frame = (pts[:, 0] < margin) | (pts[:, 0] > w-1-margin) | (pts[:, 1] < margin) | (pts[:, 1] > h-1-margin)
        if valid.mean() < .5 or on_frame.mean() > .5:
            out.append(.5)                  # lato coincidente col bordo della foto: neutro
            continue
        ii = np.clip(np.round(inside[valid]).astype(int), 0, [w-1, h-1])
        oo = np.round(outside[valid]).astype(int)
        diff = np.linalg.norm(smooth[ii[:, 1], ii[:, 0]] - smooth[oo[:, 1], oo[:, 0]], axis=-1)
        out.append(float(np.mean(diff > 6.)))
    return np.array(out)


def _score_quad(q, contour_area, gray, smooth, lab, background):
    h, w = gray.shape
    frame = float(h * w)
    area = abs(cv2.contourArea(q))
    fraction = area / frame
    if not .05 < fraction < .985:
        return None
    angles = _interior_angles(q)
    if angles.min() < 50 or angles.max() > 130:
        return None
    sides = np.linalg.norm(q - np.roll(q, -1, axis=0), axis=1)
    if sides.min() < .12 * min(h, w):
        return None
    solidity = min(contour_area / max(area, 1.), area / max(contour_area, 1.))
    if solidity < .80:
        return None
    margin = .012 * min(h, w)
    on_border = [(min(p[0], p[1]) < margin or p[0] > w-1-margin or p[1] > h-1-margin) for p in q]
    if all(on_border):
        return None                       # coincide con la foto: niente da scontornare
    # Un vero bordo del foglio e' CONTINUO: lungo tutto il lato l'interno differisce
    # dall'esterno sempre nello stesso verso. Il bordo di un blocco di testo no.
    support = _side_consistency(q, smooth, margin)
    if support is None:
        return None
    edge = .55 * support.mean() + .45 * support.min()
    # Contrasto colore tra interno e fascia esterna.
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.round(q).astype(np.int32), 255)
    k = max(3, int(min(h, w) * .025))
    inner = cv2.erode(mask, np.ones((k, k), np.uint8)) > 0
    outer = (cv2.dilate(mask, np.ones((k, k), np.uint8)) > 0) & (mask == 0)
    if inner.sum() < 50:
        return None
    if outer.sum() < 50:
        contrast, bg_like = .3, .5
    else:
        outside = lab[outer].mean(axis=0)
        contrast = float(min(1., np.linalg.norm(lab[inner].mean(axis=0) - outside) / 60.))
        # Fuori dal foglio deve esserci il piano d'appoggio (visto sul bordo della foto),
        # non altra carta: cosi' un blocco di testo interno non viene scambiato per il foglio.
        bg_like = float(max(0., 1 - np.linalg.norm(outside - background) / 30.))
    regular = float(max(0., 1 - np.abs(angles - 90).mean() / 30))
    size = float(min(1., fraction / .25))
    touch = sum(on_border) / 4.
    score = .36 * edge + .16 * contrast + .14 * bg_like + .10 * (solidity - .8) / .2 + .10 * regular + .14 * size
    score -= .10 * touch
    if edge < .45:
        score *= .6                       # bordo discontinuo: quasi certamente non e' il foglio
    return float(np.clip(score, 0, 1))


def detect_document(gray: np.ndarray, color_small: np.ndarray | None = None) -> Detection:
    """Trova il foglio (o la tessera) nella foto. Non ritaglia: restituisce angoli e affidabilita'."""
    h, w = gray.shape
    if min(h, w) < 120:
        return Detection(None, 0., 'troppo piccola', False)
    factor = min(1., DETECT_SIDE / max(h, w))
    small = cv2.resize(gray, (max(1, round(w*factor)), max(1, round(h*factor))), interpolation=cv2.INTER_AREA) \
        if factor < 1 else gray
    rgb = None
    if color_small is not None:
        rgb = cv2.resize(color_small, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) \
            if color_small.shape[:2] != small.shape else color_small
    sh, sw = small.shape
    b = max(3, int(min(sh, sw) * .03))
    ring = np.concatenate([small[:b].ravel(), small[-b:].ravel(), small[:, :b].ravel(), small[:, -b:].ravel()])
    looks_scanned = ring.mean() > 200 and ring.std() < 22
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32) if rgb is not None \
        else small[..., None].astype(np.float32)
    smooth = cv2.GaussianBlur(lab, (0, 0), 1.5).reshape(lab.shape)   # colore: vede anche bordi blu su legno
    candidates = []
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(5, int(min(sh, sw) * .02) | 1),) * 2)
    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, int(min(sh, sw) * .008) | 1),) * 2)
    masks, background = _candidate_masks(small, rgb)
    for name, mask in masks:
        m = np.uint8(mask) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, close_k)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, open_k)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
            area = cv2.contourArea(contour)
            if area < .04 * sh * sw:
                break
            quad, shape = _fit_quad(contour)
            quad = _ordered_corners(quad)
            quad[:, 0] = np.clip(quad[:, 0], 0, sw - 1)
            quad[:, 1] = np.clip(quad[:, 1], 0, sh - 1)
            score = _score_quad(quad, area, small, smooth, lab, background)
            if score is not None:
                candidates.append((score, quad, f'{name}/{shape}', abs(cv2.contourArea(quad))))
    if not candidates:
        return Detection(None, 0., 'nessun bordo', looks_scanned)
    candidates.sort(key=lambda c: -c[0])
    best = candidates[0]
    # A punteggio simile vince la sagoma piu' grande che contiene la migliore:
    # evita di tagliare fasce scure del documento (intestazioni, bande colorate).
    changed = True
    while changed:
        changed = False
        tolerance = .02 * min(sh, sw)
        for other in candidates:
            if other[3] > best[3] * 1.04 and other[0] >= best[0] - .13 and                     all(cv2.pointPolygonTest(other[1].reshape(-1, 1, 2), (float(x), float(y)), True) > -tolerance
                        for x, y in best[1]):
                best, changed = other, True
                break
    confidence = best[0]
    if looks_scanned:
        confidence = min(confidence, AUTO_CONFIDENCE - .01)   # scansione: proporre, mai tagliare da solo
    return Detection(best[1] / factor, confidence, best[2], looks_scanned)


def warp_quad(gray, corners, expand=1.0):
    """Ritaglio prospettico: il quadrilatero diventa un rettangolo dritto."""
    pts = _ordered_corners(corners)
    if expand != 1.0:
        center = pts.mean(axis=0)
        pts = center + (pts - center) * expand
    tl, tr, br, bl = pts
    tw = int(round(max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl))))
    th = int(round(max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr))))
    if min(tw, th) < 40 or tw * th > 200_000_000:
        raise ValueError('Selezione troppo piccola o non valida.')
    target = np.float32([[0, 0], [tw-1, 0], [tw-1, th-1], [0, th-1]])
    matrix = cv2.getPerspectiveTransform(pts.astype(np.float32), target)
    return cv2.warpPerspective(gray, matrix, (tw, th), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def crop_four_corners(gray, corners):
    """Ritaglio con 4 vertici scelti dall'utente in pixel sorgente."""
    pts = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    h, w = gray.shape
    if not np.isfinite(pts).all():
        raise ValueError('Vertici non validi.')
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    return warp_quad(gray, pts)


def crop_paper(gray, color_small=None):
    """Compatibilita' con la 1.6: (immagine, trovato)."""
    det = detect_document(gray, color_small)
    if det.corners is None or det.confidence < AUTO_CONFIDENCE:
        return gray, False
    return warp_quad(gray, det.corners, 1.004), True


# --------------------------------------------------------------------------- pulizia

def _deskew(gray):
    """Raddrizzamento lieve; evita rotazioni speculative del documento."""
    # Profilo delle righe: quando il testo e' dritto le righe sono nette e il profilo
    # orizzontale ha salti molto forti. Si prova un ventaglio di angoli su un'immagine ridotta.
    scale = min(1., 900. / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    binary = cv2.adaptiveThreshold(small, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    ink = float(binary.mean())
    if not .004 < ink < .30:
        return gray
    sh, sw = binary.shape
    center = (sw / 2, sh / 2)

    def sharpness(angle):
        m = cv2.getRotationMatrix2D(center, float(angle), 1.0)
        rotated = cv2.warpAffine(binary, m, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        profile = rotated.sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(profile) ** 2))

    base = sharpness(0.)
    coarse = {round(float(a), 2): sharpness(a) for a in np.arange(-6., 6.01, .5)}
    best = max(coarse, key=coarse.get)
    fine = {round(float(a), 2): sharpness(a) for a in np.arange(best - .4, best + .41, .1)}
    fine[best] = coarse[best]
    median = max(fine, key=fine.get)
    if abs(median) < 0.35 or abs(median) > 6 or fine[median] < base * 1.12:
        return gray
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w/2, h/2), median, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def _background(gray):
    """Stima il colore della carta (con ombre e luce non uniforme), senza testo.

    Le zone grandi e scure (foto del viso su una tessera, fasce colorate, immagini)
    NON sono carta: vengono escluse e la carta li' sotto viene ricostruita dalle zone
    chiare vicine. Cosi' non vengono cancellate.
    """
    h, w = gray.shape
    scale = min(1.0, 1050.0 / max(h, w))
    small = cv2.resize(gray, (max(1, round(w*scale)), max(1, round(h*scale))),
                       interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sh, sw = small.shape
    k = max(9, round(min(sh, sw) * .04) | 1)
    closed = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    gw, gh = max(4, round(sw / max(8, min(sh, sw) / 24))), max(4, round(sh / max(8, min(sh, sw) / 24)))
    grid = cv2.resize(closed, (gw, gh), interpolation=cv2.INTER_AREA)
    paper = float(np.percentile(grid, 95))
    invalid = np.uint8(grid < paper * .62) * 255
    if np.count_nonzero(invalid):
        # Un'ombra e' carta scurita: dentro si vedono ancora tratti di testo fitti.
        # Una foto o una fascia piena no. Le zone d'ombra tornano "carta".
        strokes = (cv2.subtract(closed, small) > 22).astype(np.float32)
        edge = max(2, int(min(sh, sw) * .04))
        strokes[:edge] = strokes[-edge:] = 0          # il bordo del ritaglio non e' testo
        strokes[:, :edge] = strokes[:, -edge:] = 0
        density = cv2.resize(strokes, (gw, gh), interpolation=cv2.INTER_AREA)
        valid_density = float(density[invalid == 0].mean()) if np.any(invalid == 0) else 0.
        count, labels = cv2.connectedComponents(invalid)
        for label in range(1, count):
            region = labels == label
            if density[region].mean() >= max(.02, .45 * valid_density):
                invalid[region] = 0
    if paper > 60 and 0 < np.count_nonzero(invalid) < invalid.size * .7:
        grid = cv2.inpaint(grid, invalid, 3, cv2.INPAINT_TELEA)
        bg = cv2.resize(grid, (sw, sh), interpolation=cv2.INTER_CUBIC)
        bg = cv2.GaussianBlur(bg, (0, 0), max(2., k / 3))
        # Dove la carta e' visibile vale la stima locale piu' fine.
        fine = np.where(closed >= paper * .62, closed, 0).astype(np.uint8)
        bg = np.maximum(bg, cv2.GaussianBlur(fine, (0, 0), max(2., k / 3)))
        return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    diameter = max(41, (round(min(small.shape)*.095) | 1))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter)))
    bg = cv2.GaussianBlur(bg, (0, 0), max(8, diameter/5))
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)


def analyze_content(ink: np.ndarray) -> str:
    """testo | misto | foto, in base a quanta parte dell'inchiostro e' in mezzetinte."""
    step = max(1, int(max(ink.shape) / 900))
    sample = ink[::step, ::step]
    strong = float(np.count_nonzero(sample > 70))
    mid = float(np.count_nonzero((sample > 18) & (sample <= 70)))
    total = sample.size
    if strong + mid < total * .004:
        return 'testo'
    mid_ratio = mid / (strong + mid)
    covered = (strong + mid) / total
    if mid_ratio > .55 and covered > .10:
        return 'foto'
    if mid_ratio > .40 or covered > .22:
        return 'misto'
    return 'testo'


def _lighten_solid_areas(result: np.ndarray, amount: float) -> np.ndarray:
    """Schiarisce l'interno di grandi zone scure (fasce, foto, riquadri pieni).

    Testo e linee sottili restano intatti: l'apertura morfologica conserva
    solo le zone piu' larghe di circa 2,5 mm. Il contorno della zona resta scuro.
    """
    h, w = result.shape
    px_mm = max(h, w) / 297.
    k = max(7, int(round(2.5 * px_mm)) | 1)
    dark = np.uint8(result < 150) * 255
    if np.count_nonzero(dark) < dark.size * .002:
        return result
    solid = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    edge = max(3, int(round(.7 * px_mm)) | 1)
    inner = cv2.erode(solid, cv2.getStructuringElement(cv2.MORPH_RECT, (edge, edge)))
    if not np.count_nonzero(inner):
        return result
    weight = cv2.GaussianBlur(inner.astype(np.float32) / 255., (0, 0), max(1., edge / 2.))
    density = 255. - result.astype(np.float32)
    density *= 1. - amount * weight
    return np.uint8(np.clip(255. - density, 0, 255))


def optimize(gray: np.ndarray, settings: Settings, info: dict | None = None):
    if settings.mode == 'originale':
        return gray.copy()
    gray = np.ascontiguousarray(gray, dtype=np.uint8)
    if settings.straighten:
        gray = _deskew(gray)
    if not settings.remove_background:
        return gray
    bg = _background(gray)
    ink = np.maximum(bg.astype(np.float32) - gray.astype(np.float32), 0)
    kind = analyze_content(ink)
    if info is not None:
        info['kind'] = kind
    strength = max(0, min(100, settings.strength))
    if settings.mode == 'delicato':
        threshold, saturation, gamma = 5.0 + .05*strength, 100., 1.0
    elif settings.mode == 'massimo':
        threshold, saturation, gamma = 16.0 + .07*strength, 60., .85
    elif kind == 'foto':      # automatico: conserva le mezzetinte di foto e disegni
        threshold, saturation, gamma = 7.0 + .06*strength, 110., .95
    elif kind == 'misto':
        threshold, saturation, gamma = 9.0 + .075*strength, 90., .88
    else:
        threshold, saturation, gamma = 10.0 + .085*strength, 72., .80
    density = 255.0 * np.power(np.clip((ink-threshold)/saturation, 0, 1), gamma)
    result = np.uint8(np.clip(255.0 - density, 0, 255))
    if settings.mode == 'massimo':
        result = np.where(result < 216, 0, 255).astype(np.uint8)
    # Dopo la soglia, altrimenti in Massimo risparmio le zone schiarite tornerebbero nere.
    if settings.lighten_solids and settings.mode != 'delicato':
        amount = (.35 if settings.mode == 'automatico' else .55) * (.4 + .6 * strength / 100.)
        result = _lighten_solid_areas(result, amount)
    if settings.clean_outer_edges:
        result = clear_photographic_edge_shadows(result)
    return result


def clear_photographic_edge_shadows(gray: np.ndarray) -> np.ndarray:
    """Cancella SOLO fasce fotografate molto diffuse vicino al bordo."""
    result = gray.copy()
    h, w = result.shape
    max_x = max(8, int(w * .045))
    max_y = max(8, int(h * .035))
    for axis, maxspan in ((0, max_y), (1, max_x)):
        profile = (result < 230).mean(axis=1 if axis == 0 else 0)
        kernel = np.ones(7, dtype=float)/7
        smooth = np.convolve(profile, kernel, mode='same')
        length = len(profile)
        for begin in (True, False):
            idx = np.arange(maxspan) if begin else np.arange(length-maxspan, length)
            selected = smooth[idx]
            peaks = np.flatnonzero(selected > .27)
            if len(peaks) == 0:
                continue
            peak = int(peaks[-1] if begin else peaks[0])
            inner = peak + 1 if begin else peak - 1
            if begin:
                while inner < maxspan and selected[inner] > .09:
                    inner += 1
                boundary = inner
                if boundary > int(length*.05):
                    continue
                if axis == 0:
                    result[:boundary, :] = 255
                else:
                    result[:, :boundary] = 255
            else:
                while inner >= 0 and selected[inner] > .09:
                    inner -= 1
                boundary = maxspan-1-inner
                if boundary > int(length*.05):
                    continue
                if axis == 0:
                    result[length-boundary:, :] = 255
                else:
                    result[:, length-boundary:] = 255
    return result


def darkness(arr: np.ndarray):
    # Indicatore approssimato di copertura, NON stima accurata del toner.
    step = max(1, int(max(arr.shape) / 1200))
    return float(np.mean((255.0 - arr[::step, ::step].astype(np.float32)) / 255.0) * 100.0)


def is_blank(result: np.ndarray) -> bool:
    step = max(1, int(max(result.shape) / 1200))
    sample = result[::step, ::step]
    return np.count_nonzero(sample < 160) < sample.size * .0006 and darkness(result) < .35


def looks_like_card(shape) -> bool:
    h, w = shape[:2]
    ratio = max(h, w) / max(1, min(h, w))
    return abs(ratio - CARD_RATIO) / CARD_RATIO < .07


def process_page(src: SourcePage, settings: Settings, corners=None, rotation: int = 0,
                 mode: str | None = None, detection: Detection | None = None) -> PageResult:
    """Elaborazione completa di una pagina: scontorno, rotazione, pulizia, analisi."""
    if mode:
        settings = replace(settings, mode=mode)
    working = src.gray
    used, confidence = None, 0.
    if corners is not None:
        working = crop_four_corners(working, corners)
        used, crop, text = np.asarray(corners, np.float32), 'manuale', 'Scontorno manuale'
    elif not src.is_photo:
        crop, text = 'digitale', 'PDF digitale: pagina intera'
    elif not settings.crop_document:
        crop, text = 'spento', 'Scontorno disattivato'
    else:
        det = detection or detect_document(src.gray, src.color_small)
        used, confidence = det.corners, det.confidence
        if det.corners is not None and det.confidence >= AUTO_CONFIDENCE:
            working = warp_quad(working, det.corners, 1.004)
            crop, text = 'auto', f'Scontornato automaticamente ({det.confidence:.0%})'
        elif det.looks_scanned:
            crop, text = 'intera', 'Scansione: pagina intera'
        else:
            crop, text = 'verifica', 'Da verificare: bordo del foglio incerto'
    if rotation % 4:
        working = np.ascontiguousarray(np.rot90(working, -(rotation % 4)))
    info = {}
    result = optimize(working, settings, info)
    cropped = crop in ('auto', 'manuale')
    return PageResult(image=result, crop=crop, crop_text=text, corners=used, confidence=confidence,
                      blank=is_blank(result), card=cropped and looks_like_card(result.shape),
                      kind=info.get('kind', 'testo'), before=darkness(working), after=darkness(result))


# --------------------------------------------------------------------------- impaginazione

LAYOUTS = {
    'auto': 'Automatica (consigliata)',
    'adatta': 'Adatta al foglio A4',
    'tessera': 'Tessera a grandezza reale',
    'larghezza': 'Larghezza personalizzata',
    'due': '2 per foglio',
    'quattro': '4 per foglio',
}


@dataclass
class Placement:
    index: int
    rect: tuple            # x0, y0, x1, y1 in punti
    rotate: int = 0        # 0 o 90: l'immagine viene girata per riempire meglio la cella


@dataclass
class Sheet:
    landscape: bool
    items: list

    @property
    def size(self):
        return (A4_H_PT, A4_W_PT) if self.landscape else (A4_W_PT, A4_H_PT)


def _fit(w, h, box_w, box_h, allow_rotate):
    s0 = min(box_w / w, box_h / h)
    s1 = min(box_w / h, box_h / w) if allow_rotate else 0
    if s1 > s0 * 1.02:
        return h * s1, w * s1, 90
    return w * s0, h * s0, 0


def _stack(entries, margin):
    """Impila in colonna elementi di dimensione fissa (punti), nuovo foglio se non c'e' posto."""
    sheets, column, used = [], [], 0.
    gap = 8 * MM
    avail_h = A4_H_PT - 2 * margin
    for index, w, h in entries:
        w, h = min(w, A4_W_PT - 2*margin), h * min(1., (A4_W_PT - 2*margin) / w)
        if h > avail_h:
            w, h = w * avail_h / h, avail_h
        need = h + (gap if column else 0)
        if column and used + need > avail_h:
            sheets.append(column)
            column, used, need = [], 0., h
        column.append((index, w, h))
        used += need
    if column:
        sheets.append(column)
    out = []
    for column in sheets:
        y = margin + 4 * MM if len(column) > 1 or column[0][2] < A4_H_PT * .5 else (A4_H_PT - column[0][2]) / 2
        items = []
        for index, w, h in column:
            x = (A4_W_PT - w) / 2
            items.append(Placement(index, (x, y, x + w, y + h)))
            y += h + gap
        out.append(Sheet(False, items))
    return out


def _card_entry(index, shape):
    """Tessera a grandezza reale: il lato lungo misura 85,6 mm."""
    h, w = shape
    long_side = CARD_W_MM * MM
    return (index, long_side, long_side * h / w) if w >= h else (index, long_side * w / h, long_side)


def compose_sheets(shapes, layout='auto', width_mm=85.6, margin_mm=7., cards=None):
    """shapes: lista di (altezza, larghezza) delle pagine pulite, nell'ordine di stampa."""
    margin = max(0., margin_mm) * MM
    cards = cards or [False] * len(shapes)
    sheets = []
    if layout in ('due', 'quattro'):
        per = 2 if layout == 'due' else 4
        gap = 6 * MM
        for start in range(0, len(shapes), per):
            items = []
            if per == 2:
                cell_w, cell_h = A4_W_PT - 2*margin, (A4_H_PT - 2*margin - gap) / 2
                cells = [(margin, margin), (margin, margin + cell_h + gap)]
            else:
                cell_w, cell_h = (A4_W_PT - 2*margin - gap) / 2, (A4_H_PT - 2*margin - gap) / 2
                cells = [(margin, margin), (margin + cell_w + gap, margin),
                         (margin, margin + cell_h + gap), (margin + cell_w + gap, margin + cell_h + gap)]
            for (cx, cy), index in zip(cells, range(start, min(start + per, len(shapes)))):
                h, w = shapes[index]
                tw, th, rot = _fit(w, h, cell_w, cell_h, True)
                x, y = cx + (cell_w - tw) / 2, cy + (cell_h - th) / 2
                items.append(Placement(index, (x, y, x + tw, y + th), rot))
            sheets.append(Sheet(False, items))
        return sheets
    if layout == 'tessera':
        return _stack([_card_entry(i, shape) for i, shape in enumerate(shapes)], margin)
    if layout == 'larghezza':
        width = max(1., width_mm) * MM
        return _stack([(i, width, width * h / w) for i, (h, w) in enumerate(shapes)], margin)
    # auto / adatta
    pending_cards = []

    def flush_cards():
        if pending_cards:
            sheets.extend(_stack([_card_entry(i, shapes[i]) for i in pending_cards], margin))
            pending_cards.clear()

    for i, (h, w) in enumerate(shapes):
        if layout == 'auto' and cards[i]:
            pending_cards.append(i)
            continue
        flush_cards()
        landscape = w > h * 1.08
        page_w, page_h = (A4_H_PT, A4_W_PT) if landscape else (A4_W_PT, A4_H_PT)
        scale = min((page_w - 2*margin) / w, (page_h - 2*margin) / h)
        tw, th = w * scale, h * scale
        x, y = (page_w - tw) / 2, (page_h - th) / 2
        sheets.append(Sheet(landscape, [Placement(i, (x, y, x + tw, y + th))]))
    flush_cards()
    return sheets


def _image_stream(gray, max_px=None):
    if max_px and max(gray.shape) > max_px:
        f = max_px / max(gray.shape)
        gray = cv2.resize(gray, (max(1, round(gray.shape[1]*f)), max(1, round(gray.shape[0]*f))),
                          interpolation=cv2.INTER_AREA)
    img = Image.fromarray(gray, mode='L')
    values = np.unique(gray[::7, ::7])
    if len(values) <= 2 and set(values.tolist()) <= {0, 255} and len(np.unique(gray)) <= 2:
        img = img.convert('1')          # bianco/nero puro: PDF molto piu' leggero
    buffer = io.BytesIO()
    img.save(buffer, 'PNG', optimize=max_px is None)
    return buffer.getvalue()


def build_document(sheets, images, preview_px=None):
    doc = fitz.open()
    for sheet in sheets:
        w, h = sheet.size
        page = doc.new_page(width=w, height=h)
        for item in sheet.items:
            gray = images[item.index]
            limit = None
            if preview_px:
                x0, y0, x1, y1 = item.rect
                limit = int(max(x1 - x0, y1 - y0) / 72 * preview_px * 1.3) + 8
            img = gray if not item.rotate else np.ascontiguousarray(np.rot90(gray, -1))
            page.insert_image(fitz.Rect(*item.rect), stream=_image_stream(img, limit))
    return doc


def render_sheet(sheet, images, dpi=110):
    """Anteprima del foglio, identica nel posizionamento al PDF e alla stampa."""
    doc = build_document([sheet], images, preview_px=dpi)
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), colorspace=fitz.csGRAY, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()
    finally:
        doc.close()


def save_pdf_sheets(sheets, images, destination, title=''):
    doc = build_document(sheets, images)
    try:
        if title:
            doc.set_metadata({'title': title, 'creator': 'AI Toner Saver 1.7'})
        doc.save(destination, garbage=4, deflate=True)
    finally:
        doc.close()


# Compatibilita' con la 1.6 -------------------------------------------------------

def placement_rect(gray, mode='adatta', width_mm=85.6, margin_mm=7.0):
    sheet = compose_sheets([gray.shape], 'larghezza' if mode == 'dimensioni' else 'adatta', width_mm, margin_mm)[0]
    return fitz.Rect(*sheet.items[0].rect)


def make_a4_preview(gray, mode='adatta', width_mm=85.6, margin_mm=7., dpi=110):
    sheet = compose_sheets([gray.shape], 'larghezza' if mode == 'dimensioni' else 'adatta', width_mm, margin_mm)[0]
    return render_sheet(sheet, [gray], dpi)


def save_pdf(pages, destination: str, dpi: int = 250, mode='adatta', width_mm=85.6, margin_mm=7.):
    images = [g for g, _ in pages]
    layout = 'larghezza' if mode == 'dimensioni' else mode
    save_pdf_sheets(compose_sheets([g.shape for g in images], layout, width_mm, margin_mm), images, destination)
