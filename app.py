"""AI Toner Saver 1.7.1 - Windows 11 | Trascina, controlla, stampa."""
import getpass
import os
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import (Qt, QObject, Signal, QSettings, QSize, QTimer, QPointF, QRectF, QRect,
                            QLockFile, QDir, QFileSystemWatcher)
from PySide6.QtGui import (QImage, QPixmap, QPainter, QPageSize, QAction, QIcon, QPen, QColor, QBrush,
                           QPainterPath, QPolygonF, QKeySequence, QShortcut, QFont)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrinterInfo
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QCheckBox, QSlider,
                               QDoubleSpinBox, QProgressBar, QSplitter, QStatusBar, QDialog, QListWidget,
                               QListWidgetItem, QAbstractItemView, QMenu, QFrame, QSizePolicy, QToolButton)

import engine
from engine import (Settings, SourcePage, PageResult, collect_files, iter_pages, process_page, detect_document,
                    compose_sheets, render_sheet, save_pdf_sheets, build_document, LAYOUTS, SUPPORTED, fitz)

VERSION = '1.7.1'
APP_NAME = 'AI Toner Saver ' + VERSION
APP_DIR = Path(__file__).resolve().parent
MODES = ['automatico', 'delicato', 'massimo', 'originale']
MODE_LABELS = ['Automatico intelligente', 'Delicato (firme/timbri)', 'Massimo risparmio', 'Originale']



def _portable_dir():
    """Versione portatile (Python dentro la cartella): tutto resta nella cartella, se scrivibile."""
    if not (APP_DIR / 'runtime' / 'python.exe').exists():
        return None
    try:
        probe = APP_DIR / '.prova_scrittura'
        probe.write_text('ok')
        probe.unlink()
        return APP_DIR
    except OSError:
        return None             # cartella in sola lettura: si usa il profilo di Windows


PORTABLE_DIR = _portable_dir()
if PORTABLE_DIR:
    import hashlib
    SERVER_NAME = 'AITonerSaver17-' + hashlib.md5(str(APP_DIR).lower().encode()).hexdigest()[:10]
    LOG_DIR = PORTABLE_DIR
    LOCK_DIR = PORTABLE_DIR
else:
    SERVER_NAME = 'AITonerSaver17-' + getpass.getuser()
    LOG_DIR = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'AITonerSaver'
    LOCK_DIR = Path(tempfile.gettempdir())


def make_settings():
    if PORTABLE_DIR:
        return QSettings(str(PORTABLE_DIR / 'impostazioni.ini'), QSettings.Format.IniFormat)
    return QSettings('LocalTools', 'AITonerSaver17')

STYLE = '''
QMainWindow, QWidget { background: #0f1724; color: #e5ecf7; font-family: 'Segoe UI'; font-size: 10pt; }
QPushButton { background: #2563eb; color: white; border: none; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #3976ff; } QPushButton:disabled { background: #334055; color: #8995a8; }
QPushButton#ghost { background: #1d2a40; border: 1px solid #34445e; }
QPushButton#ghost:hover { background: #26375a; }
QPushButton#good { background: #15803d; } QPushButton#good:hover { background: #16a34a; }
QPushButton#warn { background: #b45309; } QPushButton#warn:hover { background: #d97706; }
QComboBox, QDoubleSpinBox { background: #1c2a40; border: 1px solid #3d4a60; border-radius: 5px; padding: 5px; }
QComboBox QAbstractItemView { background: #1c2a40; selection-background-color: #2563eb; }
QCheckBox { padding: 2px; }
QLabel#panel { background: #152036; border: 1px solid #2f3e56; border-radius: 8px; color: #8fa3c0; }
QLabel#banner { background: #3b2a0a; border: 1px solid #b45309; border-radius: 6px; padding: 6px 10px; color: #fde68a; }
QListWidget { background: #131e31; border: 1px solid #2f3e56; border-radius: 8px; padding: 4px; }
QListWidget::item { border-radius: 6px; padding: 4px; margin: 2px; }
QListWidget::item:selected { background: #1e3a6e; }
QProgressBar { border: 1px solid #3d4a60; border-radius: 4px; text-align: center; height: 16px; }
QProgressBar::chunk { background: #22c55e; }
QStatusBar { color: #b4c5dc; }
QMenu { background: #1c2a40; border: 1px solid #3d4a60; } QMenu::item:selected { background: #2563eb; }
QSlider::groove:horizontal { height: 5px; background: #2f3e56; border-radius: 2px; }
QSlider::handle:horizontal { background: #60a5fa; width: 14px; margin: -5px 0; border-radius: 7px; }
'''


def gray_pixmap(gray: np.ndarray, max_dimension=1400):
    h, w = gray.shape
    factor = min(1.0, max_dimension / max(h, w))
    if factor < 1:
        gray = cv2.resize(gray, (max(1, int(w*factor)), max(1, int(h*factor))), interpolation=cv2.INTER_AREA)
    gray = np.ascontiguousarray(gray)
    height, width = gray.shape
    qimg = QImage(gray.data, width, height, gray.strides[0], QImage.Format.Format_Grayscale8)
    return QPixmap.fromImage(qimg.copy()), factor


def rgb_pixmap(rgb: np.ndarray):
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QPixmap.fromImage(QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy())


@dataclass
class PageItem:
    uid: int
    src: SourcePage
    corners: object = None          # angoli manuali (pixel sorgente) o None = automatico
    rotation: int = 0               # quarti di giro in senso orario
    mode: str | None = None         # modalita' solo per questa pagina
    include: bool | None = None     # None = decide il programma (pagine vuote escluse)
    result: PageResult | None = None
    detection: object = None
    version: int = 0
    error: str = ''
    dpi: int = 0

    @property
    def name(self):
        return Path(self.src.source).name

    @property
    def key(self):
        return (self.src.source.lower(), self.src.number)


class Bridge(QObject):
    page_loaded = Signal(object)
    load_error = Signal(str, str)
    source_done = Signal(str)
    load_finished = Signal()
    result = Signal(int, int, object, object, str)


# --------------------------------------------------------------------------- viste

class ImageView(QLabel):
    def __init__(self, hint):
        super().__init__(hint)
        self.setObjectName('panel')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 300)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._pix = None
        self._hint = hint

    def set_array(self, arr, corners=None, color='#22c55e', rotation=0):
        if arr is None:
            self._pix = None
            self.clear()
            self.setText(self._hint)
            return
        pix, factor = gray_pixmap(arr)
        if corners is not None:
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            polygon = QPolygonF([QPointF(float(x) * factor, float(y) * factor) for x, y in corners])
            path = QPainterPath()
            path.addRect(QRectF(pix.rect()))
            path.addPolygon(polygon)
            path.setFillRule(Qt.FillRule.OddEvenFill)
            painter.fillPath(path, QColor(0, 0, 0, 110))
            pen = QPen(QColor(color), max(2., pix.width() / 350.))
            painter.setPen(pen)
            painter.drawPolygon(polygon)
            painter.end()
        self._pix = pix
        self._refresh()

    def _refresh(self):
        if self._pix is not None:
            self.setPixmap(self._pix.scaled(self.size() - QSize(14, 14), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()


class CornerCanvas(QWidget):
    """Quattro maniglie da trascinare, con lente d'ingrandimento sul punto."""

    def __init__(self, gray, corners, max_w, max_h):
        super().__init__()
        h, w = gray.shape
        self.scale = min(1., max_w / w, max_h / h)
        small = cv2.resize(gray, (max(1, int(w*self.scale)), max(1, int(h*self.scale))), interpolation=cv2.INTER_AREA)
        self.pix, _ = gray_pixmap(small, max_dimension=99999)
        self.full_w, self.full_h = w, h
        self.setFixedSize(self.pix.size())
        self.setMouseTracking(True)
        self.drag = None
        self.set_corners(corners)

    def set_corners(self, corners):
        self.points = [QPointF(float(x) * self.scale, float(y) * self.scale) for x, y in corners]
        self.update()

    def corners(self):
        return [(p.x() / self.scale, p.y() / self.scale) for p in self.points]

    def _nearest(self, pos):
        d = [(p - pos).manhattanLength() for p in self.points]
        i = int(np.argmin(d))
        return i, d[i]

    def _clamp(self, pos):
        return QPointF(min(max(pos.x(), 0.), self.width() - 1.), min(max(pos.y(), 0.), self.height() - 1.))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self._clamp(event.position())
            i, _ = self._nearest(pos)
            self.drag = i
            self.points[i] = pos
            self.update()

    def mouseMoveEvent(self, event):
        pos = self._clamp(event.position())
        if self.drag is not None:
            self.points[self.drag] = pos
        else:
            i, d = self._nearest(pos)
            self.setCursor(Qt.CursorShape.SizeAllCursor if d < 30 else Qt.CursorShape.CrossCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self.pix)
        ordered = engine._ordered_corners([(p.x(), p.y()) for p in self.points])
        polygon = QPolygonF([QPointF(float(x), float(y)) for x, y in ordered])
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        path.addPolygon(polygon)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        painter.fillPath(path, QColor(0, 0, 0, 120))
        painter.setPen(QPen(QColor('#38bdf8'), 2))
        painter.drawPolygon(polygon)
        for i, p in enumerate(self.points):
            painter.setPen(QPen(QColor('white'), 2))
            painter.setBrush(QBrush(QColor('#2563eb' if i != self.drag else '#f59e0b')))
            painter.drawEllipse(p, 9, 9)
        if self.drag is not None:
            p = self.points[self.drag]
            src = QRectF(p.x() - 30, p.y() - 30, 60, 60)
            size = 180
            target = QRectF(10, 10, size, size) if p.x() > self.width() / 2 or p.y() > self.height() / 2 \
                else QRectF(self.width() - size - 10, self.height() - size - 10, size, size)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPixmap(target, self.pix, src)
            painter.setPen(QPen(QColor('#f59e0b'), 2))
            painter.drawRect(target)
            c = target.center()
            painter.drawLine(QPointF(c.x() - 12, c.y()), QPointF(c.x() + 12, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - 12), QPointF(c.x(), c.y() + 12))
        painter.end()


class CornerEditor(QDialog):
    def __init__(self, item: PageItem, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Bordi del documento - ' + item.name)
        self.item = item
        gray = item.src.gray
        h, w = gray.shape
        self.full = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
        start = item.corners if item.corners is not None else \
            (item.result.corners if item.result is not None and item.result.corners is not None else None)
        if start is None and item.detection is not None and item.detection.corners is not None:
            start = item.detection.corners
        if start is None:
            start = [(w * .06, h * .06), (w * .94, h * .06), (w * .94, h * .94), (w * .06, h * .94)]
        screen = QApplication.primaryScreen().availableGeometry()
        v = QVBoxLayout(self)
        info = QLabel('Trascina i 4 punti blu sugli angoli del documento. Mentre trascini compare una lente '
                      'per essere precisi. L\'ordine dei punti non conta.')
        info.setWordWrap(True)
        v.addWidget(info)
        self.canvas = CornerCanvas(gray, start, screen.width() - 120, screen.height() - 230)
        v.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        auto = QPushButton('Rileva di nuovo')
        auto.setObjectName('ghost')
        auto.clicked.connect(self.redetect)
        whole = QPushButton('Usa tutta la foto')
        whole.setObjectName('ghost')
        whole.clicked.connect(lambda: self.canvas.set_corners(self.full))
        cancel = QPushButton('Annulla')
        cancel.setObjectName('ghost')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('Applica')
        ok.setObjectName('good')
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(auto)
        row.addWidget(whole)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

    def redetect(self):
        det = detect_document(self.item.src.gray, self.item.src.color_small)
        if det.corners is None:
            QMessageBox.information(self, 'Nessun bordo', 'Non trovo un bordo affidabile: posiziona i punti a mano.')
            return
        self.canvas.set_corners(det.corners)

    def corners(self):
        return self.canvas.corners()


# --------------------------------------------------------------------------- finestra

class MainWindow(QMainWindow):
    def __init__(self, initial=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self.stored = make_settings()
        self.items: dict[int, PageItem] = {}
        self.next_uid = 1
        self.bridge = Bridge()
        self.bridge.page_loaded.connect(self.on_page_loaded)
        self.bridge.load_error.connect(self.on_load_error)
        self.bridge.source_done.connect(self.on_source_done)
        self.bridge.load_finished.connect(self.on_load_finished)
        self.bridge.result.connect(self.on_result)
        workers = max(1, min(6, (os.cpu_count() or 2) - 1))
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.load_queue = []
        self.load_lock = threading.Lock()
        self.loading = False
        self.pending = 0
        self.loaded_sources = set()       # sorgenti lette completamente
        self.auto_sources = {}            # sorgente -> cartella di uscita (cartella automatica)
        self.load_errors = []
        self.sheets = []
        self.sheet_items = []
        self._make_ui()
        self.reprocess_timer = QTimer(self, singleShot=True, interval=350, timeout=self.reprocess_all)
        self.recompose_timer = QTimer(self, singleShot=True, interval=250, timeout=self.recompose)
        self.watch_timer = QTimer(self, interval=2000, timeout=self.scan_watch_folder)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self.watch_timer.start())
        self.watch_seen = {}
        self._restore_watch()
        if initial:
            self.add_paths(initial)

    # ---------------------------------------------------------------- interfaccia
    def _make_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(16, 12, 16, 8)
        main.setSpacing(9)

        head = QHBoxLayout()
        title = QLabel(f'AI TONER SAVER <span style="color:#60a5fa">{VERSION}</span>'
                       + (' <span style="color:#86efac;font-size:10pt">portatile</span>' if PORTABLE_DIR else ''))
        title.setStyleSheet('font-size: 17pt; font-weight: 700; color: #f0f6ff;')
        head.addWidget(title)
        sub = QLabel('Trascina qui PDF, foto o intere cartelle: scontorno, pulizia e impaginazione sono automatici.')
        sub.setStyleSheet('color:#93a7c4; padding-left: 14px;')
        head.addWidget(sub, 1)
        main.addLayout(head)

        row = QHBoxLayout()
        main.addLayout(row)
        self.open_btn = self._button('📂 Aggiungi file', self.select_files, row)
        self.folder_btn = self._button('🗂 Aggiungi cartella', self.select_folder, row, 'ghost')
        self.clear_btn = self._button('🧹 Svuota', self.clear_all, row, 'ghost')
        row.addSpacing(14)
        self.corners_btn = self._button('📐 Bordi…', self.edit_corners, row, 'ghost')
        self.rot_l_btn = self._button('⟲', lambda: self.rotate_selected(-1), row, 'ghost')
        self.rot_r_btn = self._button('⟳', lambda: self.rotate_selected(1), row, 'ghost')
        self.remove_btn = self._button('🗑 Togli pagina', self.remove_selected, row, 'ghost')
        self.rot_l_btn.setToolTip('Ruota a sinistra (Ctrl+L)')
        self.rot_r_btn.setToolTip('Ruota a destra (Ctrl+R)')
        row.addStretch(1)
        self.save_btn = self._button('💾 Salva PDF', self.export_pdf, row, 'good')
        self.print_btn = self._button('🖨 Stampa', self.print_pages, row, 'good')

        settings = QHBoxLayout()
        main.addLayout(settings)
        settings.addWidget(QLabel('Modalità:'))
        self.mode = QComboBox()
        self.mode.addItems(MODE_LABELS)
        self.mode.setCurrentIndex(int(self.stored.value('mode', 0)))
        settings.addWidget(self.mode)
        settings.addWidget(QLabel('Intensità:'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(int(self.stored.value('strength', 65)))
        self.strength.setFixedWidth(120)
        settings.addWidget(self.strength)
        self.intensity = QLabel(f'{self.strength.value()}%')
        self.intensity.setFixedWidth(36)
        settings.addWidget(self.intensity)
        self.crop_document = self._check('Scontorno automatico', 'crop', True, settings)
        self.straighten = self._check('Raddrizza', 'straighten', True, settings)
        self.clean_edges = self._check('Pulisci bordi', 'edges', True, settings)
        self.lighten = self._check('Schiarisci zone piene', 'lighten', True, settings,
                                   'Alleggerisce l\'interno di fasce nere, foto e riquadri pieni: il testo resta intatto.')
        self.skip_blank = self._check('Salta pagine vuote', 'skipblank', True, settings)
        settings.addWidget(QLabel('Risoluzione PDF:'))
        self.dpi = QComboBox()
        self.dpi.addItems(['200', '250', '300'])
        self.dpi.setCurrentText(str(self.stored.value('dpi', '250')))
        settings.addWidget(self.dpi)
        settings.addStretch(1)

        layout_row = QHBoxLayout()
        main.addLayout(layout_row)
        layout_row.addWidget(QLabel('Impaginazione A4:'))
        self.layout_mode = QComboBox()
        self.layout_keys = list(LAYOUTS)
        self.layout_mode.addItems(list(LAYOUTS.values()))
        self.layout_mode.setCurrentIndex(int(self.stored.value('layout', 0)))
        self.layout_mode.setToolTip('Automatica: i documenti riempiono il foglio (verticale o orizzontale), '
                                    'le tessere vengono stampate a grandezza reale e raggruppate fronte/retro.')
        layout_row.addWidget(self.layout_mode)
        self.width_label = QLabel('Larghezza:')
        layout_row.addWidget(self.width_label)
        self.content_width = QDoubleSpinBox()
        self.content_width.setRange(10, 196)
        self.content_width.setDecimals(1)
        self.content_width.setSuffix(' mm')
        self.content_width.setValue(float(self.stored.value('width', 85.6)))
        layout_row.addWidget(self.content_width)
        layout_row.addWidget(QLabel('Margine:'))
        self.margin = QDoubleSpinBox()
        self.margin.setRange(3, 25)
        self.margin.setSuffix(' mm')
        self.margin.setValue(float(self.stored.value('margin', 7)))
        layout_row.addWidget(self.margin)
        layout_row.addSpacing(20)
        self.watch_check = QCheckBox('Cartella automatica')
        self.watch_check.setToolTip('Ogni PDF o foto copiato in questa cartella viene pulito e salvato da solo '
                                    'nella sottocartella "Puliti".')
        layout_row.addWidget(self.watch_check)
        self.watch_label = QLabel('')
        self.watch_label.setStyleSheet('color:#93a7c4')
        layout_row.addWidget(self.watch_label)
        self.watch_btn = self._button('Scegli…', self.choose_watch_folder, layout_row, 'ghost')
        layout_row.addStretch(1)

        self.banner = QWidget()
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(0, 0, 0, 0)
        self.banner_label = QLabel('')
        self.banner_label.setObjectName('banner')
        bl.addWidget(self.banner_label, 1)
        self.banner_btn = self._button('Controlla ora', self.review_next, bl, 'warn')
        self.banner.hide()
        main.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(splitter, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel('PAGINE  (trascina per riordinare, spunta = da stampare)'))
        self.page_list = QListWidget()
        self.page_list.setIconSize(QSize(72, 96))
        self.page_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.page_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.page_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self.page_menu)
        self.page_list.currentRowChanged.connect(lambda _r: self.show_current())
        self.page_list.itemChanged.connect(self.on_item_changed)
        self.page_list.model().rowsMoved.connect(lambda *a: self.recompose())
        self.page_list.itemDoubleClicked.connect(lambda _i: self.edit_corners())
        lv.addWidget(self.page_list, 1)
        splitter.addWidget(left)

        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.before_title = QLabel('ORIGINALE  (in verde il bordo trovato)')
        mv.addWidget(self.before_title)
        self.before = ImageView('Trascina qui i tuoi file\n\nPDF · JPG · PNG · TIFF · WEBP · cartelle intere\n\n'
                                'oppure usa «Aggiungi file»')
        mv.addWidget(self.before, 1)
        splitter.addWidget(mid)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.after_title = QLabel('FOGLIO A4 CHE VERRÀ STAMPATO')
        rv.addWidget(self.after_title)
        self.after = ImageView('Qui apparirà il foglio A4 pulito')
        rv.addWidget(self.after, 1)
        splitter.addWidget(right)
        splitter.setSizes([300, 540, 540])

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(320)
        self.progress.setValue(0)
        bottom.addWidget(self.progress)
        self.summary = QLabel('')
        self.summary.setStyleSheet('color:#86efac;font-weight:600')
        bottom.addWidget(self.summary, 1)
        main.addLayout(bottom)
        note = QLabel('Verifica sempre nell\'anteprima numeri, firme, timbri e testi piccoli. La copertura indicata '
                      'è una stima sull\'immagine, non una misura del toner. I PDF esportati sono immagini.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#c9a24a; font-size: 9pt')
        main.addWidget(note)
        self.setStatusBar(QStatusBar())

        for widget in (self.mode,):
            widget.currentIndexChanged.connect(self.settings_changed)
        self.strength.valueChanged.connect(lambda v: (self.intensity.setText(f'{v}%'), self.settings_changed()))
        for box in (self.crop_document, self.straighten, self.clean_edges, self.lighten):
            box.toggled.connect(self.settings_changed)
        self.skip_blank.toggled.connect(self.on_skip_blank)
        self.dpi.currentTextChanged.connect(self.on_dpi_changed)
        self.layout_mode.currentIndexChanged.connect(self.layout_changed)
        self.content_width.valueChanged.connect(self.layout_changed)
        self.margin.valueChanged.connect(self.layout_changed)
        self.watch_check.toggled.connect(self.toggle_watch)

        QShortcut(QKeySequence(QKeySequence.StandardKey.Open), self, self.select_files)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Save), self, self.export_pdf)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Print), self, self.print_pages)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.page_list, self.remove_selected)
        QShortcut(QKeySequence('Ctrl+L'), self, lambda: self.rotate_selected(-1))
        QShortcut(QKeySequence('Ctrl+R'), self, lambda: self.rotate_selected(1))
        QShortcut(QKeySequence('Ctrl+B'), self, self.edit_corners)
        self.layout_changed(store=False)
        self._enable()

    def _button(self, text, slot, layout, name=None):
        b = QPushButton(text)
        if name:
            b.setObjectName(name)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _check(self, text, key, default, layout, tip=''):
        c = QCheckBox(text)
        c.setChecked(str(self.stored.value(key, default)).lower() in ('true', '1'))
        c.setProperty('key', key)
        if tip:
            c.setToolTip(tip)
        layout.addWidget(c)
        return c

    def _enable(self):
        has = self.page_list.count() > 0
        ready = any(self._included(it) for it in self.items.values())
        selected = self.current_item() is not None
        for b in (self.corners_btn, self.rot_l_btn, self.rot_r_btn, self.remove_btn):
            b.setEnabled(selected)
        self.clear_btn.setEnabled(has)
        self.save_btn.setEnabled(ready and self.pending == 0 and not self.loading)
        self.print_btn.setEnabled(ready and self.pending == 0 and not self.loading)

    # ---------------------------------------------------------------- impostazioni
    def params(self) -> Settings:
        return Settings(mode=MODES[self.mode.currentIndex()], strength=self.strength.value(),
                        straighten=self.straighten.isChecked(), dpi=int(self.dpi.currentText()),
                        crop_document=self.crop_document.isChecked(),
                        clean_outer_edges=self.clean_edges.isChecked(), lighten_solids=self.lighten.isChecked())

    def store_settings(self):
        s = self.stored
        s.setValue('mode', self.mode.currentIndex())
        s.setValue('strength', self.strength.value())
        s.setValue('dpi', self.dpi.currentText())
        s.setValue('layout', self.layout_mode.currentIndex())
        s.setValue('width', self.content_width.value())
        s.setValue('margin', self.margin.value())
        for box in (self.crop_document, self.straighten, self.clean_edges, self.lighten, self.skip_blank):
            s.setValue(box.property('key'), box.isChecked())

    def settings_changed(self, *args):
        self.store_settings()
        if self.items:
            self.reprocess_timer.start()

    def layout_changed(self, *args, store=True):
        key = self.layout_keys[self.layout_mode.currentIndex()]
        self.content_width.setVisible(key == 'larghezza')
        self.width_label.setVisible(key == 'larghezza')
        if store:
            self.store_settings()
        self.recompose()

    def on_skip_blank(self, *args):
        self.store_settings()
        for row in range(self.page_list.count()):
            self._update_row(row)
        self.recompose()

    def on_dpi_changed(self, *args):
        self.store_settings()
        pdfs = sorted({it.src.source for it in self.items.values() if it.src.source.lower().endswith('.pdf')})
        if pdfs:
            self.statusBar().showMessage('Rilettura dei PDF alla nuova risoluzione…')
            self.reload_sources(pdfs)

    # ---------------------------------------------------------------- caricamento
    def select_files(self):
        start = str(self.stored.value('last_dir', str(Path.home() / 'Documents')))
        pattern = ' '.join('*' + e for e in sorted(SUPPORTED))
        paths, _ = QFileDialog.getOpenFileNames(self, 'Aggiungi documenti (puoi selezionarne molti)', start,
                                                f'Documenti e foto ({pattern});;Tutti i file (*.*)')
        if paths:
            self.stored.setValue('last_dir', str(Path(paths[0]).parent))
            self.add_paths(paths)

    def select_folder(self):
        start = str(self.stored.value('last_dir', str(Path.home() / 'Documents')))
        folder = QFileDialog.getExistingDirectory(self, 'Aggiungi tutti i documenti di una cartella', start)
        if folder:
            self.stored.setValue('last_dir', folder)
            self.add_paths([folder])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def add_paths(self, paths, auto_out=None, restore=None):
        files = collect_files(paths)
        if not files:
            if paths:
                self.statusBar().showMessage('Nessun file PDF o immagine riconosciuto.')
            return
        if auto_out:
            for f in files:
                self.auto_sources[f.lower()] = auto_out
        dpi = int(self.dpi.currentText())
        with self.load_lock:
            self.load_queue.extend((f, restore or {}, dpi) for f in files)
            start = not self.loading
            self.loading = True
        self.statusBar().showMessage(f'Lettura di {len(files)} file…')
        if start:
            threading.Thread(target=self._loader, daemon=True).start()
        self._enable()

    def _loader(self):
        while True:
            with self.load_lock:
                if not self.load_queue:
                    self.loading = False
                    break
                path, restore, dpi = self.load_queue.pop(0)
            try:
                for page in iter_pages(path, dpi):
                    self.bridge.page_loaded.emit((page, restore))
            except Exception as exc:
                self.bridge.load_error.emit(path, str(exc))
            self.bridge.source_done.emit(path)
        self.bridge.load_finished.emit()

    def on_page_loaded(self, payload):
        page, restore = payload
        item = PageItem(self.next_uid, page)
        self.next_uid += 1
        old = restore.get((page.source.lower(), page.number))
        if old:
            item.corners, item.rotation, item.mode, item.include = old
        self.items[item.uid] = item
        row = QListWidgetItem()
        row.setData(Qt.ItemDataRole.UserRole, item.uid)
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        row.setSizeHint(QSize(240, 108))
        self.page_list.blockSignals(True)
        self.page_list.addItem(row)
        self.page_list.blockSignals(False)
        self._update_row(self.page_list.count() - 1)
        if self.page_list.currentRow() < 0:
            self.page_list.setCurrentRow(0)
        self.submit(item)

    def on_load_error(self, path, message):
        self.load_errors.append(f'{Path(path).name}: {message}')

    def on_source_done(self, path):
        self.loaded_sources.add(path.lower())
        self._check_auto_export(path)

    def on_load_finished(self):
        if self.load_errors:
            QMessageBox.warning(self, 'Alcuni file non sono stati letti', '\n'.join(self.load_errors[:15]))
            self.load_errors = []
        self._progress()
        self._enable()

    def reload_sources(self, sources):
        keep = {}
        dpi_now = int(self.dpi.currentText())
        for uid in [self._uid(r) for r in range(self.page_list.count())]:
            it = self.items[uid]
            if it.src.source in sources:
                corners = it.corners
                if corners is not None and getattr(it, 'dpi', None):
                    corners = [(x * dpi_now / it.dpi, y * dpi_now / it.dpi) for x, y in corners]
                keep[it.key] = (corners, it.rotation, it.mode, it.include)
        self._remove_uids([u for u, it in self.items.items() if it.src.source in sources])
        self.add_paths(sources, restore=keep)

    # ---------------------------------------------------------------- elaborazione
    def submit(self, item: PageItem):
        item.version += 1
        item.dpi = int(self.dpi.currentText())
        version, settings = item.version, self.params()
        corners, rotation, mode, detection = item.corners, item.rotation, item.mode, item.detection
        src = item.src
        self.pending += 1
        self._progress()

        def job():
            if item.version != version:          # superato da una modifica piu' recente
                self.bridge.result.emit(item.uid, version, None, None, 'superato')
                return
            try:
                det = detection
                if det is None and src.is_photo and corners is None and settings.crop_document:
                    det = detect_document(src.gray, src.color_small)
                res = process_page(src, settings, corners, rotation, mode, det)
                self.bridge.result.emit(item.uid, version, res, det, '')
            except Exception as exc:
                self.bridge.result.emit(item.uid, version, None, None, f'{exc}\n{traceback.format_exc()}')
        self.pool.submit(job)

    def reprocess_all(self):
        for row in range(self.page_list.count()):
            self.submit(self.items[self._uid(row)])

    def on_result(self, uid, version, result, detection, error):
        self.pending = max(0, self.pending - 1)
        item = self.items.get(uid)
        if item is not None and version == item.version:
            if detection is not None:
                item.detection = detection
            item.result, item.error = result, error
            if error:
                self._log(error)
            row = self._row_of(uid)
            if row is not None:
                self._update_row(row)
            if self.current_item() is item:
                self.show_current()
            self._check_auto_export(item.src.source)
        self._progress()
        self.recompose_timer.start()
        if self.pending == 0:
            self._update_banner()
        self._enable()

    def _progress(self):
        total = self.page_list.count()
        if self.pending == 0 and not self.loading:
            self.progress.setValue(100 if total else 0)
            if total:
                self.statusBar().showMessage('Pronto. Controlla l\'anteprima e poi Salva o Stampa.', 6000)
        else:
            done = max(0, total - self.pending)
            self.progress.setValue(int(done * 100 / max(1, total)))
            self.statusBar().showMessage(f'Elaborazione… {done}/{total} pagine')

    # ---------------------------------------------------------------- elenco pagine
    def _uid(self, row):
        return self.page_list.item(row).data(Qt.ItemDataRole.UserRole)

    def _row_of(self, uid):
        for row in range(self.page_list.count()):
            if self._uid(row) == uid:
                return row
        return None

    def current_item(self):
        row = self.page_list.currentRow()
        if row < 0 or row >= self.page_list.count():
            return None
        return self.items.get(self._uid(row))

    def selected_items(self):
        rows = sorted({self.page_list.row(i) for i in self.page_list.selectedItems()})
        if not rows and self.current_item() is not None:
            return [self.current_item()]
        return [self.items[self._uid(r)] for r in rows]

    def _included(self, item):
        if item.result is None:
            return False
        if item.include is not None:
            return item.include
        return not (self.skip_blank.isChecked() and item.result.blank)

    def _update_row(self, row):
        # Cambiare icona o testo emette itemChanged: senza blocco sembrerebbe un clic sulla spunta.
        self.page_list.blockSignals(True)
        try:
            self._fill_row(row)
        finally:
            self.page_list.blockSignals(False)

    def _fill_row(self, row):
        widget = self.page_list.item(row)
        item = self.items[self._uid(row)]
        res = item.result
        label = item.name if len(item.name) < 30 else item.name[:27] + '…'
        multi = sum(1 for it in self.items.values() if it.src.source == item.src.source) > 1
        lines = [label + (f' · pag. {item.src.number}' if multi else '')]
        color = '#93a7c4'
        if item.error:
            lines.append('✖ errore di elaborazione')
            color = '#f87171'
        elif res is None:
            lines.append('⏳ in elaborazione…')
        else:
            if res.blank:
                lines.append('○ pagina vuota')
            elif res.crop in ('auto', 'manuale'):
                lines.append('✓ ' + ('scontornata' if res.crop == 'auto' else 'bordi manuali'))
                color = '#86efac'
            elif res.crop == 'verifica':
                lines.append('⚠ controlla i bordi')
                color = '#fbbf24'
            else:
                lines.append('• pagina intera')
            extra = []
            if res.card:
                extra.append('tessera')
            extra.append({'testo': 'testo', 'misto': 'testo e immagini', 'foto': 'foto/grafica'}[res.kind])
            if item.mode:
                extra.append(MODE_LABELS[MODES.index(item.mode)].split(' (')[0].lower())
            if item.rotation % 4:
                extra.append(f'ruotata {90 * (item.rotation % 4)}°')
            lines.append(' · '.join(extra))
            thumb = res.image
            h, w = thumb.shape
            f = 192. / max(h, w)
            small = cv2.resize(thumb, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
            widget.setIcon(QIcon(gray_pixmap(small)[0]))
        if res is None and widget.icon().isNull():
            g = item.src.gray
            f = 192. / max(g.shape)
            small = cv2.resize(g, (max(1, int(g.shape[1] * f)), max(1, int(g.shape[0] * f))), interpolation=cv2.INTER_AREA)
            widget.setIcon(QIcon(gray_pixmap(small)[0]))
        widget.setText('\n'.join(lines))
        widget.setForeground(QBrush(QColor(color)))
        widget.setCheckState(Qt.CheckState.Checked if self._included(item) else Qt.CheckState.Unchecked)
        widget.setToolTip(item.src.source + (f'\n{res.crop_text}' if res else ''))

    def on_item_changed(self, widget):
        item = self.items.get(widget.data(Qt.ItemDataRole.UserRole))
        if item is None or item.result is None:
            return
        checked = widget.checkState() == Qt.CheckState.Checked
        if checked != self._included(item):
            item.include = checked
            self.recompose()
            self._enable()

    def page_menu(self, pos):
        if self.current_item() is None:
            return
        menu = QMenu(self)
        menu.addAction('📐 Bordi del documento…', self.edit_corners)
        menu.addAction('↺ Torna allo scontorno automatico', self.reset_selected)
        menu.addSeparator()
        menu.addAction('⟲ Ruota a sinistra', lambda: self.rotate_selected(-1))
        menu.addAction('⟳ Ruota a destra', lambda: self.rotate_selected(1))
        menu.addAction('⟳ Capovolgi (180°)', lambda: self.rotate_selected(2))
        sub = menu.addMenu('Modalità solo per questa pagina')
        sub.addAction('Come impostazione generale', lambda: self.set_mode_selected(None))
        for key, label in zip(MODES, MODE_LABELS):
            sub.addAction(label, lambda k=key: self.set_mode_selected(k))
        menu.addSeparator()
        menu.addAction('🗑 Togli dall\'elenco', self.remove_selected)
        menu.exec(self.page_list.mapToGlobal(pos))

    def rotate_selected(self, quarter):
        for item in self.selected_items():
            item.rotation = (item.rotation + quarter) % 4
            self.submit(item)
            self._update_row(self._row_of(item.uid))

    def set_mode_selected(self, mode):
        for item in self.selected_items():
            item.mode = mode
            self.submit(item)

    def reset_selected(self):
        for item in self.selected_items():
            item.corners = None
            self.submit(item)

    def remove_selected(self):
        items = self.selected_items()
        if items:
            self._remove_uids([it.uid for it in items])

    def _remove_uids(self, uids):
        uids = set(uids)
        self.page_list.blockSignals(True)
        for row in reversed(range(self.page_list.count())):
            if self._uid(row) in uids:
                self.page_list.takeItem(row)
        self.page_list.blockSignals(False)
        for uid in uids:
            item = self.items.pop(uid, None)
            if item:
                item.version += 1000     # i risultati in arrivo vengono ignorati
        self.show_current()
        self.recompose()
        self._update_banner()
        self._enable()

    def clear_all(self):
        self._remove_uids(list(self.items))
        self.loaded_sources.clear()
        self.before.set_array(None)
        self.after.set_array(None)
        self.summary.setText('')
        self._progress()

    def edit_corners(self):
        item = self.current_item()
        if item is None:
            return
        editor = CornerEditor(item, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            item.corners = editor.corners()
            item.include = None if item.include is False else item.include
            self.submit(item)
            self._update_row(self._row_of(item.uid))

    def review_next(self):
        for row in range(self.page_list.count()):
            item = self.items[self._uid(row)]
            if item.result is not None and item.result.crop == 'verifica':
                self.page_list.setCurrentRow(row)
                self.edit_corners()
                return

    def _update_banner(self):
        todo = [it for it in self.items.values() if it.result is not None and it.result.crop == 'verifica']
        if todo:
            self.banner_label.setText(f'⚠  {len(todo)} pagin{"a" if len(todo) == 1 else "e"} con bordo incerto: '
                                      'per sicurezza sono state lasciate intere. Controlla e sistema gli angoli.')
            self.banner.show()
        else:
            self.banner.hide()

    # ---------------------------------------------------------------- anteprima
    def ordered_included(self):
        out = []
        for row in range(self.page_list.count()):
            item = self.items[self._uid(row)]
            if self._included(item):
                out.append(item)
        return out

    def layout_params(self):
        return (self.layout_keys[self.layout_mode.currentIndex()], self.content_width.value(), self.margin.value())

    def make_sheets(self, items):
        layout, width, margin = self.layout_params()
        return compose_sheets([it.result.image.shape for it in items], layout, width, margin,
                              [it.result.card for it in items])

    def recompose(self):
        self.sheet_items = self.ordered_included()
        self.sheets = self.make_sheets(self.sheet_items) if self.sheet_items else []
        if self.sheet_items:
            before = np.mean([it.result.before for it in self.sheet_items])
            after = np.mean([it.result.after for it in self.sheet_items])
            excluded = sum(1 for it in self.items.values() if it.result is not None and not self._included(it))
            text = f'{len(self.sheet_items)} pagine → {len(self.sheets)} fogli A4   ·   copertura stimata ' \
                   f'{before:.1f}% → {after:.1f}%'
            if excluded:
                text += f'   ·   {excluded} escluse'
            self.summary.setText(text)
        else:
            self.summary.setText('')
        self.show_current()
        self._enable()

    def show_current(self):
        item = self.current_item()
        self._enable()
        if item is None:
            self.before.set_array(None)
            self.after.set_array(None)
            return
        res = item.result
        corners, color = None, '#22c55e'
        if item.corners is not None:
            corners, color = item.corners, '#38bdf8'
        elif res is not None and res.corners is not None:
            corners = res.corners
            color = '#22c55e' if res.crop == 'auto' else '#f59e0b'
        self.before.set_array(item.src.gray, corners, color)
        self.before_title.setText(f'ORIGINALE · {item.name}' + (f' · pag. {item.src.number}' if item.src.number > 1 else '')
                                  + (f'   —   {res.crop_text}' if res else ''))
        if res is None:
            self.after.set_array(None)
            self.after.setText('Elaborazione in corso…')
            return
        index = next((i for i, s in enumerate(self.sheet_items) if s is item), None)
        if index is None:
            preview = compose_sheets([res.image.shape], 'adatta', *self.layout_params()[1:])[0]
            self.after.set_array(render_sheet(preview, [res.image], 140))
            self.after_title.setText('ESCLUSA DALLA STAMPA (spunta la pagina per includerla)')
            return
        for n, sheet in enumerate(self.sheets):
            if any(p.index == index for p in sheet.items):
                self.after.set_array(render_sheet(sheet, [it.result.image for it in self.sheet_items], 140))
                self.after_title.setText(f'FOGLIO A4 {n + 1} DI {len(self.sheets)}'
                                         + ('  · orizzontale' if sheet.landscape else ''))
                break

    # ---------------------------------------------------------------- uscita
    def _unique(self, path: Path):
        if not path.exists():
            return path
        for n in range(2, 999):
            candidate = path.with_name(f'{path.stem} ({n}){path.suffix}')
            if not candidate.exists():
                return candidate
        return path

    def export_pdf(self):
        items = self.ordered_included()
        if not items:
            return
        sources = list(dict.fromkeys(it.src.source for it in items))
        per_file = False
        if len(sources) > 1:
            box = QMessageBox(self)
            box.setWindowTitle('Salva PDF')
            box.setText(f'Hai {len(items)} pagine da {len(sources)} file diversi. Come vuoi salvarle?')
            one = box.addButton('Un unico PDF', QMessageBox.ButtonRole.AcceptRole)
            many = box.addButton('Un PDF per ogni file', QMessageBox.ButtonRole.AcceptRole)
            box.addButton('Annulla', QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() not in (one, many):
                return
            per_file = box.clickedButton() is many
        try:
            if per_file:
                folder = QFileDialog.getExistingDirectory(self, 'Cartella dove salvare i PDF puliti',
                                                          str(Path(sources[0]).parent))
                if not folder:
                    return
                saved = []
                for source in sources:
                    group = [it for it in items if it.src.source == source]
                    target = self._unique(Path(folder) / (Path(source).stem + '_PULITO.pdf'))
                    save_pdf_sheets(self.make_sheets(group), [it.result.image for it in group], str(target),
                                    Path(source).stem)
                    saved.append(target.name)
                self.statusBar().showMessage(f'{len(saved)} PDF salvati in {folder}')
                QMessageBox.information(self, 'PDF pronti', f'Salvati {len(saved)} PDF in:\n{folder}')
            else:
                first = Path(sources[0])
                name = first.stem + '_PULITO.pdf' if len(sources) == 1 else 'Documenti_PULITI.pdf'
                proposal = str(self._unique(first.with_name(name)))
                filename, _ = QFileDialog.getSaveFileName(self, 'Salva documento pulito', proposal, 'PDF (*.pdf)')
                if not filename:
                    return
                save_pdf_sheets(self.make_sheets(items), [it.result.image for it in items], filename, Path(filename).stem)
                self.statusBar().showMessage('PDF salvato: ' + filename)
                box = QMessageBox(self)
                box.setWindowTitle('PDF pronto')
                box.setText('File salvato:\n' + filename)
                open_btn = box.addButton('Apri il PDF', QMessageBox.ButtonRole.ActionRole)
                box.addButton('OK', QMessageBox.ButtonRole.AcceptRole)
                box.exec()
                if box.clickedButton() is open_btn:
                    os.startfile(filename)
        except Exception as exc:
            QMessageBox.critical(self, 'Errore salvataggio', str(exc))

    def print_pages(self):
        items = self.ordered_included()
        if not items:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName('AI Toner Saver - ' + items[0].name)
        names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        preferred = str(self.stored.value('printer', ''))
        match = preferred if preferred in names else next(
            (n for n in names if 'laserjet' in n.lower() and any(m in n.lower() for m in ('m14', 'm15', 'm16', 'm17'))),
            None)
        if match:
            printer.setPrinterName(match)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle('Stampa')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not printer.isValid():
            QMessageBox.warning(self, 'Stampante non valida', 'Installa/configura la stampante in Windows e riprova.')
            return
        self.stored.setValue('printer', printer.printerName())
        sheets = self.make_sheets(items)
        document = build_document(sheets, [it.result.image for it in items])
        try:
            painter = QPainter()
            if not painter.begin(printer):
                raise RuntimeError('Il driver non ha accettato il lavoro di stampa.')
            try:
                for i, page in enumerate(document):
                    if i:
                        printer.newPage()
                    pix = page.get_pixmap(dpi=300, colorspace=fitz.csGRAY, alpha=False)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
                    if pix.width > pix.height:          # foglio orizzontale su carta verticale
                        arr = np.rot90(arr, 1)
                    arr = np.ascontiguousarray(arr)
                    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0],
                                 QImage.Format.Format_Grayscale8).copy()
                    rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                    fit = img.size().scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
                    left = rect.x() + (rect.width() - fit.width()) / 2
                    top = rect.y() + (rect.height() - fit.height()) / 2
                    painter.drawImage(QRectF(left, top, fit.width(), fit.height()), img)
            finally:
                painter.end()
            self.statusBar().showMessage(f'{len(sheets)} fogli inviati alla stampante {printer.printerName()}.')
        except Exception as exc:
            QMessageBox.critical(self, 'Errore di stampa', str(exc))
        finally:
            document.close()

    # ---------------------------------------------------------------- cartella automatica
    def _restore_watch(self):
        folder = str(self.stored.value('watch_folder', ''))
        self.watch_label.setText(self._short(folder) if folder else '(nessuna cartella)')
        if folder and str(self.stored.value('watch_on', 'false')).lower() == 'true' and Path(folder).is_dir():
            self.watch_check.setChecked(True)

    def _short(self, folder):
        return folder if len(folder) < 40 else '…' + folder[-38:]

    def choose_watch_folder(self):
        start = str(self.stored.value('watch_folder', '')) or str(Path.home() / 'Documents')
        folder = QFileDialog.getExistingDirectory(self, 'Cartella automatica: i file messi qui vengono puliti da soli',
                                                  start)
        if folder:
            self.stored.setValue('watch_folder', folder)
            self.watch_label.setText(self._short(folder))
            if self.watch_check.isChecked():
                self.toggle_watch(False)
                self.toggle_watch(True)
            else:
                self.watch_check.setChecked(True)

    def toggle_watch(self, on):
        folder = str(self.stored.value('watch_folder', ''))
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watch_timer.stop()
        if on and not (folder and Path(folder).is_dir()):
            self.watch_check.blockSignals(True)
            self.watch_check.setChecked(False)
            self.watch_check.blockSignals(False)
            self.choose_watch_folder()
            return
        self.stored.setValue('watch_on', bool(on))
        if on:
            self.watcher.addPath(folder)
            out = Path(folder) / 'Puliti'
            # I file gia' presenti e gia' puliti non vengono rifatti.
            self.watch_seen = {}
            for f in collect_files([folder]):
                if (out / (Path(f).stem + '_PULITO.pdf')).exists():
                    self.watch_seen[f.lower()] = 'fatto'
            self.watch_timer.start()
            self.statusBar().showMessage(f'Cartella automatica attiva: {folder}', 8000)

    def scan_watch_folder(self):
        folder = str(self.stored.value('watch_folder', ''))
        if not self.watch_check.isChecked() or not Path(folder).is_dir():
            self.watch_timer.stop()
            return
        ready, waiting = [], False
        for f in collect_files([folder]):
            key = f.lower()
            if self.watch_seen.get(key) == 'fatto':
                continue
            try:
                stat = os.stat(f)
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime)
            if self.watch_seen.get(key) == signature and time.time() - stat.st_mtime > 1.5:
                self.watch_seen[key] = 'fatto'
                ready.append(f)
            else:
                self.watch_seen[key] = signature      # file ancora in copia: si riprova tra poco
                waiting = True
        if ready:
            out = Path(folder) / 'Puliti'
            out.mkdir(exist_ok=True)
            self.add_paths(ready, auto_out=str(out))
        if not waiting:
            self.watch_timer.stop()

    def _check_auto_export(self, source):
        out = self.auto_sources.get(source.lower())
        if not out or source.lower() not in self.loaded_sources:
            return
        group = [self.items[self._uid(r)] for r in range(self.page_list.count())
                 if self.items[self._uid(r)].src.source.lower() == source.lower()]
        if not group or any(it.result is None and not it.error for it in group):
            return
        del self.auto_sources[source.lower()]
        group = [it for it in group if self._included(it)]
        if not group:
            return
        target = self._unique(Path(out) / (Path(source).stem + '_PULITO.pdf'))
        try:
            save_pdf_sheets(self.make_sheets(group), [it.result.image for it in group], str(target), Path(source).stem)
            self.statusBar().showMessage(f'Cartella automatica: salvato {target.name}', 10000)
        except Exception as exc:
            self._log(f'Salvataggio automatico {source}: {exc}')

    # ---------------------------------------------------------------- varie
    def _log(self, text):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / 'errori.log', 'a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')
        except OSError:
            pass

    def receive_paths(self, paths):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if paths:
            self.add_paths(paths)

    def closeEvent(self, event):
        self.store_settings()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


# --------------------------------------------------------------------------- avvio

def excepthook(kind, value, tb):
    text = ''.join(traceback.format_exception(kind, value, tb))
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / 'errori.log', 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')
    except OSError:
        pass
    if QApplication.instance():
        QMessageBox.critical(None, 'Errore imprevisto', f'{value}\n\nDettagli in {LOG_DIR / "errori.log"}')


def send_to_running(paths, attempts=1):
    message = ('\n'.join(paths) if paths else '').encode('utf-8') + b'\n<<FINE>>'
    for _ in range(attempts):
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(500):
            socket.write(message)
            socket.flush()
            socket.waitForBytesWritten(2000)
            socket.disconnectFromServer()
            return True
        time.sleep(.25)
    return False


def main():
    sys.excepthook = excepthook
    args = [a for a in sys.argv[1:] if a.strip()]
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    # Un solo programma aperto: i file scelti da Esplora risorse (anche molti insieme) arrivano
    # tutti nella stessa finestra.
    lock = QLockFile(str(LOCK_DIR / ('.' + SERVER_NAME + '.lock')))
    lock.setStaleLockTime(0)
    if not lock.tryLock(150):
        if send_to_running(args, attempts=24):
            return 0
        lock.removeStaleLockFile()
        lock.tryLock(150)
    window = MainWindow(args or None)
    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)
    buffers = {}

    def on_connection():
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            buffers[sock] = b''

            def read(s=sock):
                buffers[s] += bytes(s.readAll())
                if buffers[s].endswith(b'<<FINE>>'):
                    text = buffers.pop(s)[:-len(b'<<FINE>>')].decode('utf-8', 'replace')
                    window.receive_paths([p for p in text.splitlines() if p.strip()])
            sock.readyRead.connect(read)
    server.newConnection.connect(on_connection)
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == '__main__':
    sys.exit(main())
