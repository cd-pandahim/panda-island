import os
import sys
import time
import math
import ctypes
import asyncio
import calendar
import threading
import datetime as dt

import psutil
from PyQt6.QtCore import (Qt, QTimer, QRectF, QPointF, QSizeF, QVariantAnimation,
                          QAbstractAnimation, QEasingCurve, QObject, pyqtSignal)
from PyQt6.QtGui import (QPainter, QColor, QFont, QFontMetrics, QPainterPath, QPen,
                         QPixmap, QIcon, QLinearGradient)
from PyQt6.QtWidgets import QApplication, QWidget, QMenu, QSystemTrayIcon

try:
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlayStatus)
        from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    except ImportError:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlayStatus)
        from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    HAS_MEDIA = True
except ImportError:
    HAS_MEDIA = False

APP_NAME = "Panda Island"
USE_24H = False
WEEK_STARTS_SUNDAY = True
CORNER_RADIUS = 14
TOP_MARGIN = 8
AUTO_COLLAPSE_MS = 5000
POP_ON_TRACK_CHANGE = True

CLOCK, MEDIA, TIMER, CAL, SYS = range(5)
PAGE_COUNT = 5
EXPANDED = {CLOCK: (320, 118), MEDIA: (410, 150), TIMER: (340, 146),
            CAL: (330, 210), SYS: (350, 126)}

FONTS = ["Segoe UI Variable Display", "Segoe UI", "SF Pro Display", "Helvetica Neue", "Arial"]
WHITE = QColor(255, 255, 255)
BLACK = QColor(0, 0, 0)
GRAY = QColor(255, 255, 255, 140)
ACCENT = QColor("#64D2FF")
GREEN = QColor("#30D158")
AMBER = QColor("#FF9F0A")
RED = QColor("#FF453A")
BLUE = QColor("#0A84FF")
PURPLE = QColor("#BF5AF2")

APP_NAMES = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave",
             "opera": "Opera", "spotify": "Spotify", "vlc": "VLC", "zunemusic": "Media Player",
             "applemusic": "Apple Music", "youtubemusic": "YouTube Music"}

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PandaIsland"


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def pretty_app(app_id):
    low = (app_id or "").lower()
    for key, name in APP_NAMES.items():
        if key in low:
            return name
    return (app_id or "").split("!")[0].split(".")[0].capitalize()


def fmt_time(seconds):
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def startup_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except Exception:
        return False


def set_startup(enable):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass


def already_running():
    if sys.platform != "win32":
        return False
    ctypes.windll.kernel32.CreateMutexW(None, False, "PandaIslandSingleInstance")
    return ctypes.windll.kernel32.GetLastError() == 183


class Countdown:
    def __init__(self, length=300):
        self.length = length
        self.base = float(length)
        self.mark = 0.0
        self.running = False

    def remaining(self):
        if not self.running:
            return self.base
        return max(0.0, self.base - (time.monotonic() - self.mark))

    def start(self):
        if self.base <= 0:
            self.base = float(self.length)
        self.mark = time.monotonic()
        self.running = True

    def pause(self):
        self.base = self.remaining()
        self.running = False

    def reset(self):
        self.running = False
        self.base = float(self.length)

    def adjust(self, delta):
        new = max(60, min(self.length + delta, 6 * 3600))
        change = new - self.length
        self.length = new
        if self.running:
            self.base = max(1.0, self.remaining() + change)
            self.mark = time.monotonic()
        else:
            self.base = float(new)


class Stopwatch:
    def __init__(self):
        self.base = 0.0
        self.mark = 0.0
        self.running = False

    def elapsed(self):
        if not self.running:
            return self.base
        return self.base + time.monotonic() - self.mark

    def start(self):
        self.mark = time.monotonic()
        self.running = True

    def pause(self):
        self.base = self.elapsed()
        self.running = False

    def reset(self):
        self.base = 0.0
        self.running = False


class MediaWatcher(QObject):
    updated = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.session = None
        self._key = None
        self._art = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._poll())
        self.loop.run_forever()

    async def _read_art(self, ref):
        try:
            if ref is None:
                return None
            stream = await ref.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
            data = bytearray(buf.length)
            DataReader.from_buffer(buf).read_bytes(data)
            return bytes(data)
        except Exception:
            return None

    async def _poll(self):
        mgr = None
        while True:
            info = {}
            try:
                if mgr is None:
                    mgr = await SessionManager.request_async()
                s = mgr.get_current_session()
                self.session = s
                if s is not None:
                    props = await s.try_get_media_properties_async()
                    playback = s.get_playback_info()
                    tl = s.get_timeline_properties()
                    key = (props.title, props.artist, props.album_title)
                    if key != self._key:
                        self._key = key
                        self._art = await self._read_art(props.thumbnail)
                    start = tl.start_time.total_seconds()
                    info = dict(
                        key=key,
                        title=props.title or "",
                        artist=props.artist or props.album_artist or "",
                        app=pretty_app(s.source_app_user_model_id),
                        playing=playback.playback_status == PlayStatus.PLAYING,
                        pos=max(0.0, tl.position.total_seconds() - start),
                        dur=max(0.0, tl.end_time.total_seconds() - start),
                        art=self._art,
                    )
            except Exception:
                mgr = None
            self.updated.emit(info)
            await asyncio.sleep(1)

    def command(self, name):
        asyncio.run_coroutine_threadsafe(self._cmd(name), self.loop)

    async def _cmd(self, name):
        s = self.session
        if s is None:
            return
        try:
            await {"toggle": s.try_toggle_play_pause_async,
                   "next": s.try_skip_next_async,
                   "prev": s.try_skip_previous_async}[name]()
        except Exception:
            pass


class Island(QWidget):
    def __init__(self, icon):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self.expanded = False
        self.page = CLOCK
        self.media = {}
        self.media_t = time.monotonic()
        self.art = None
        self._art_key = None
        self.marquee_t0 = time.monotonic()
        self._first_update = True
        self._last_wheel = 0.0
        self.stats = dict(cpu=0.0, ram=0.0, batt=None, plugged=False)
        psutil.cpu_percent(None)

        self.mode = "timer"
        self.cd = Countdown()
        self.sw = Stopwatch()
        self.alarm = False
        self._beeps = 0

        self.content = 0.0
        self.page_fade = 1.0
        self._cur = QSizeF(*self.collapsed_size())

        self.size_anim = QVariantAnimation(self)
        self.size_anim.valueChanged.connect(self._apply_size)
        self.content_anim = QVariantAnimation(self)
        self.content_anim.valueChanged.connect(lambda v: self._set("content", v))
        self.fade_anim = QVariantAnimation(self)
        self.fade_anim.valueChanged.connect(lambda v: self._set("page_fade", v))

        self.idle = QTimer(self, singleShot=True)
        self.idle.timeout.connect(self._idle_timeout)
        self.tick = QTimer(self, interval=1000)
        self.tick.timeout.connect(self._tick)
        self.tick.start()
        self.pulse = QTimer(self, interval=200)
        self.pulse.timeout.connect(self._pulse)
        self.pulse.start()
        self.fx = QTimer(self, interval=33)
        self.fx.timeout.connect(self.update)

        self.menu = self.build_menu()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

        self.watcher = None
        if HAS_MEDIA:
            self.watcher = MediaWatcher()
            self.watcher.updated.connect(self.on_media)

        self._apply_size(self._cur)
        self._tick()

    def showEvent(self, e):
        super().showEvent(e)
        if sys.platform == "win32":
            self.strip_chrome()

    def strip_chrome(self):
        try:
            dwm = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            for attr, value in ((2, 1), (33, 1), (34, 0xFFFFFFFE)):
                v = ctypes.c_uint(value)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
        except Exception:
            pass

    def build_menu(self):
        menu = QMenu()
        menu.setStyleSheet("QMenu{background:#1c1c1e;color:white;border:1px solid #333;"
                           "border-radius:8px;padding:4px}QMenu::item{padding:6px 18px;border-radius:5px}"
                           "QMenu::item:selected{background:#3a3a3c}"
                           "QMenu::separator{height:1px;background:#333;margin:4px 8px}")
        self.login_action = menu.addAction("Launch at login")
        self.login_action.setCheckable(True)
        self.login_action.triggered.connect(set_startup)
        menu.aboutToShow.connect(lambda: self.login_action.setChecked(startup_enabled()))
        menu.addSeparator()
        menu.addAction(f"Quit {APP_NAME}", QApplication.quit)
        return menu

    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.expanded:
                self.collapse()
            else:
                self.expand(CLOCK)

    def _set(self, name, value):
        setattr(self, name, float(value))
        self.update()

    def face(self, px, weight=QFont.Weight.Normal):
        f = QFont()
        f.setFamilies(FONTS)
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    def has_media(self):
        return bool(self.media.get("title") or self.media.get("artist"))

    def is_playing(self):
        return self.has_media() and bool(self.media.get("playing"))

    def cur_pos(self):
        m = self.media
        if not m:
            return 0.0
        pos = m["pos"] + (time.monotonic() - self.media_t if m["playing"] else 0)
        return max(0.0, min(pos, m["dur"])) if m["dur"] > 0 else pos

    def timer_info(self):
        if self.cd.running:
            left = self.cd.remaining()
            return fmt_time(math.ceil(left)), min(1.0, left / self.cd.length)
        if self.sw.running:
            return fmt_time(self.sw.elapsed()), None
        return None

    def collapsed_size(self):
        w = 196 if self.is_playing() else 128
        if self.timer_info():
            w += 18
        return (w, 34)

    def target_size(self):
        return EXPANDED[self.page] if self.expanded else self.collapsed_size()

    def _apply_size(self, size):
        self._cur = QSizeF(size)
        w, h = max(40, round(size.width())), max(20, round(size.height()))
        geo = QApplication.primaryScreen().geometry()
        self.setGeometry(geo.x() + (geo.width() - w) // 2, geo.y() + TOP_MARGIN, w, h)

    def _animate(self, anim, start, end, ms, curve=QEasingCurve.Type.OutCubic):
        anim.stop()
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.start()

    def _retarget(self):
        end = QSizeF(*self.target_size())
        running = self.size_anim.state() == QAbstractAnimation.State.Running
        if running and self.size_anim.endValue() == end:
            return
        if not running and self._cur == end:
            return
        self.size_anim.stop()
        self.size_anim.setStartValue(QSizeF(self._cur))
        self.size_anim.setEndValue(end)
        if self.expanded:
            curve = QEasingCurve(QEasingCurve.Type.OutBack)
            curve.setOvershoot(1.0)
            self.size_anim.setDuration(480)
        else:
            curve = QEasingCurve(QEasingCurve.Type.OutCubic)
            self.size_anim.setDuration(320)
        self.size_anim.setEasingCurve(curve)
        self.size_anim.start()

    def _sync_timers(self):
        media_fx = self.is_playing() and (not self.expanded or self.page == MEDIA)
        timer_fx = self.expanded and self.page == TIMER and (self.sw.running or self.alarm)
        want = media_fx or timer_fx
        if want and not self.fx.isActive():
            self.fx.start()
        elif not want and self.fx.isActive():
            self.fx.stop()

    def expand(self, page):
        was = self.expanded
        self.expanded = True
        self.page = page
        if was:
            self._animate(self.fade_anim, 0.0, 1.0, 240)
        else:
            self.page_fade = 1.0
        self._animate(self.content_anim, self.content, 1.0, 400, QEasingCurve.Type.InQuad)
        self._retarget()
        self._sync_timers()
        if not self.underMouse() and not self.alarm:
            self.idle.start(AUTO_COLLAPSE_MS)

    def collapse(self):
        self.expanded = False
        self.idle.stop()
        self._animate(self.content_anim, self.content, 0.0, 150, QEasingCurve.Type.OutQuad)
        self._retarget()
        self._sync_timers()

    def set_page(self, page):
        self.page = page % PAGE_COUNT
        self._animate(self.fade_anim, 0.0, 1.0, 240)
        self._retarget()
        self._sync_timers()

    def _idle_timeout(self):
        if self.expanded and not self.alarm and not self.underMouse():
            self.collapse()

    def _tick(self):
        self.stats["cpu"] = psutil.cpu_percent(None)
        self.stats["ram"] = psutil.virtual_memory().percent
        try:
            b = psutil.sensors_battery()
            self.stats["batt"] = b.percent if b else None
            self.stats["plugged"] = bool(b and b.power_plugged)
        except Exception:
            self.stats["batt"] = None
        if self.size_anim.state() != QAbstractAnimation.State.Running:
            self._apply_size(self._cur)
        self.update()

    def _pulse(self):
        if self.cd.running and self.cd.remaining() <= 0:
            self.cd.running = False
            self.cd.base = 0.0
            self.start_alarm()
        if self.cd.running or self.sw.running or self.alarm:
            self.update()

    def start_alarm(self):
        self.alarm = True
        self.mode = "timer"
        self._beeps = 4
        self._beep()
        self.idle.stop()
        if self.expanded:
            self.set_page(TIMER)
        else:
            self.expand(TIMER)
        self._sync_timers()

    def _beep(self):
        if not self.alarm or self._beeps <= 0:
            return
        self._beeps -= 1
        QApplication.beep()
        QTimer.singleShot(650, self._beep)

    def dismiss_alarm(self):
        self.alarm = False
        self.cd.reset()
        self.collapse()

    def timer_action(self, name):
        active = self.cd if self.mode == "timer" else self.sw
        if name == "tab_timer":
            self.mode = "timer"
        elif name == "tab_watch":
            self.mode = "watch"
        elif name == "minus" and self.mode == "timer":
            self.cd.adjust(-60)
        elif name == "plus" and self.mode == "timer":
            self.cd.adjust(60)
        elif name == "reset":
            active.reset()
        elif name == "toggle":
            if active.running:
                active.pause()
            else:
                active.start()
        self._retarget()
        self._sync_timers()
        self.update()

    def on_media(self, m):
        old_key = self.media.get("key")
        self.media = m or {}
        self.media_t = time.monotonic()
        if m and m.get("key") != self._art_key:
            self._art_key = m.get("key")
            self.marquee_t0 = time.monotonic()
            pm = QPixmap()
            if m.get("art"):
                pm.loadFromData(m["art"])
            self.art = pm if not pm.isNull() else None
        elif not m:
            self.art, self._art_key = None, None

        if (POP_ON_TRACK_CHANGE and not self._first_update and self.has_media()
                and m.get("key") != old_key and m.get("playing") and not self.expanded):
            self.expand(MEDIA)
            self.idle.start(4500)
        self._first_update = False
        if not self.expanded:
            self._retarget()
        self._sync_timers()
        self.update()

    def enterEvent(self, e):
        self.idle.stop()

    def leaveEvent(self, e):
        if self.expanded and not self.alarm:
            self.idle.start(AUTO_COLLAPSE_MS)

    def wheelEvent(self, e):
        d = e.angleDelta()
        dy = d.y() or d.x()
        now = time.monotonic()
        if dy == 0 or now - self._last_wheel < 0.28:
            return
        self._last_wheel = now
        if not self.expanded:
            self.expand(MEDIA)
        else:
            self.set_page(self.page + (1 if dy < 0 else -1))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.menu.exec(e.globalPosition().toPoint())
            return
        pos = e.position()
        if self.alarm:
            self.dismiss_alarm()
            return
        w, h = self.width(), self.height()
        if self.expanded and self.page == MEDIA and self.has_media() and self.watcher:
            r = self.media_rects(w, h)
            for name in ("prev", "toggle", "next"):
                if r[name].contains(pos):
                    if name == "toggle":
                        self.media["pos"] = self.cur_pos()
                        self.media["playing"] = not self.media["playing"]
                        self.media_t = time.monotonic()
                        self._sync_timers()
                    self.watcher.command(name)
                    self.update()
                    return
        if self.expanded and self.page == TIMER:
            r = self.timer_rects(w, h)
            for name, rect in r.items():
                if name in ("minus", "plus") and self.mode != "timer":
                    continue
                if rect.contains(pos):
                    self.timer_action(name)
                    return
        if self.expanded:
            self.collapse()
        else:
            self.expand(CLOCK)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        radius = min(rect.height() / 2, CORNER_RADIUS)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, BLACK)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawPath(path)
        p.setClipPath(path)

        c = self.content
        if c < 0.99:
            p.setOpacity(1 - c)
            self.paint_collapsed(p, w, h)
        if c > 0.01:
            p.setOpacity(c * self.page_fade)
            pages = (self.paint_clock, self.paint_media, self.paint_timer,
                     self.paint_calendar, self.paint_system)
            pages[self.page](p, w, h)
            p.setOpacity(c)
            self.paint_dots(p, w, h)

    def draw_art(self, p, r, radius):
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(r, radius, radius)
        p.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        if self.art:
            s = min(self.art.width(), self.art.height())
            src = QRectF((self.art.width() - s) / 2, (self.art.height() - s) / 2, s, s)
            p.drawPixmap(r, self.art, src)
        else:
            p.fillRect(r, QColor("#2c2c2e"))
            p.setPen(GRAY)
            p.setFont(self.face(int(r.height() * 0.5)))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "\u266a")
        p.restore()

    def paint_collapsed(self, p, w, h):
        info = self.timer_info()
        if not (self.has_media() or info):
            return
        cy = h / 2
        right = w - 14
        if info:
            text, frac = info
            f = self.face(13, QFont.Weight.Medium)
            tw = QFontMetrics(f).horizontalAdvance("00:00" if len(text) <= 5 else "0:00:00")
            p.setFont(f)
            p.setPen(WHITE)
            p.drawText(QRectF(right - tw, 0, tw, h),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            right -= tw + 10
        if self.has_media():
            self.draw_art(p, QRectF(10, cy - 10, 20, 20), 6)
            playing = self.is_playing()
            t = time.monotonic()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ACCENT)
            for i in range(4):
                bh = 4 + 11 * abs(math.sin(t * 5 + i * 1.4)) if playing else 3
                x = right - 3 - (3 - i) * 5.5
                p.drawRoundedRect(QRectF(x, cy - bh / 2, 3, bh), 1.5, 1.5)
            title = self.media.get("title") or self.media.get("artist") or ""
            x0, x1 = 38, right - 3 - 16.5 - 8
            if title and x1 - x0 > 30:
                self.draw_marquee(p, title, QRectF(x0, cy - 10, x1 - x0, 20), playing)
        elif info:
            ring = QRectF(12, cy - 8, 16, 16)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 50), 2.2))
            p.drawEllipse(ring)
            if frac is not None:
                p.setPen(QPen(ACCENT, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(ring, 90 * 16, int(-360 * 16 * frac))

    def draw_marquee(self, p, text, area, scrolling):
        f = self.face(12, QFont.Weight.Medium)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(text)
        aw, ah = area.width(), area.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(aw * dpr), int(ah * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        q.setFont(f)
        q.setPen(QColor(255, 255, 255, 225))
        base = ah / 2 + (fm.ascent() - fm.descent()) / 2
        if tw <= aw:
            q.drawText(QPointF((aw - tw) / 2, base), text)
        elif not scrolling:
            q.drawText(QRectF(0, 0, aw, ah), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, int(aw)))
        else:
            span = tw + 36
            shift = max(0.0, time.monotonic() - self.marquee_t0 - 2.0) * 12 % span
            x = 6 - shift
            q.drawText(QPointF(x, base), text)
            q.drawText(QPointF(x + span, base), text)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            fade = 10 / aw
            mask = QLinearGradient(0, 0, aw, 0)
            mask.setColorAt(0, QColor(0, 0, 0, int(255 * (1 - min(1.0, shift / 6)))))
            mask.setColorAt(fade, QColor(0, 0, 0, 255))
            mask.setColorAt(1 - fade, QColor(0, 0, 0, 255))
            mask.setColorAt(1, QColor(0, 0, 0, 0))
            q.fillRect(QRectF(0, 0, aw, ah), mask)
        q.end()
        p.drawPixmap(area.topLeft(), pm)

    def paint_clock(self, p, w, h):
        now = dt.datetime.now()
        main = now.strftime("%H:%M") if USE_24H else now.strftime("%I:%M").lstrip("0")
        suffix = now.strftime(":%S") + ("" if USE_24H else now.strftime(" %p"))
        f1, f2 = self.face(44, QFont.Weight.Light), self.face(15, QFont.Weight.Medium)
        w1 = QFontMetrics(f1).horizontalAdvance(main)
        w2 = QFontMetrics(f2).horizontalAdvance(":00" + ("" if USE_24H else " PM"))
        x = (w - (w1 + 6 + w2)) / 2
        p.setPen(WHITE)
        p.setFont(f1)
        p.drawText(QPointF(x, 62), main)
        p.setPen(GRAY)
        p.setFont(f2)
        p.drawText(QPointF(x + w1 + 6, 62), suffix)
        p.setFont(self.face(14, QFont.Weight.Medium))
        p.drawText(QRectF(0, 74, w, 22), Qt.AlignmentFlag.AlignCenter, f"{now:%A, %B} {now.day}")

    def media_rects(self, w, h):
        tx, tw = 114, w - 114 - 22
        cx, cy = tx + tw / 2, 82
        return {
            "art": QRectF(20, 20, 76, 76), "tx": tx, "tw": tw,
            "prev": QRectF(cx - 62, cy - 16, 32, 32),
            "toggle": QRectF(cx - 20, cy - 20, 40, 40),
            "next": QRectF(cx + 30, cy - 16, 32, 32),
            "bar": QRectF(62, 118, w - 124, 4),
        }

    def timer_rects(self, w, h):
        cx = w / 2
        return {
            "tab_timer": QRectF(cx - 90, 8, 86, 22),
            "tab_watch": QRectF(cx + 4, 8, 86, 22),
            "minus": QRectF(16, 47, 50, 26),
            "plus": QRectF(w - 66, 47, 50, 26),
            "reset": QRectF(cx - 50, 90, 38, 38),
            "toggle": QRectF(cx + 12, 90, 38, 38),
        }

    def draw_icon(self, p, kind, c, s):
        x, y = c.x() - s / 2, c.y() - s / 2
        p.setPen(QPen(WHITE, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(WHITE)
        path = QPainterPath()
        if kind == "play":
            path.moveTo(x + s * .22, y + 1)
            path.lineTo(x + s * .92, y + s / 2)
            path.lineTo(x + s * .22, y + s - 1)
            path.closeSubpath()
            p.drawPath(path)
        elif kind == "pause":
            p.drawRoundedRect(QRectF(x + s * .18, y + 1, s * .22, s - 2), 2, 2)
            p.drawRoundedRect(QRectF(x + s * .60, y + 1, s * .22, s - 2), 2, 2)
        elif kind == "stop":
            p.drawRoundedRect(QRectF(x + 1, y + 1, s - 2, s - 2), 2.5, 2.5)
        elif kind in ("next", "prev"):
            p.save()
            if kind == "prev":
                p.translate(2 * c.x(), 0)
                p.scale(-1, 1)
            path.moveTo(x + 1, y + 2)
            path.lineTo(x + s * .72, y + s / 2)
            path.lineTo(x + 1, y + s - 2)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QPointF(x + s - 1.5, y + 2), QPointF(x + s - 1.5, y + s - 2))
            p.restore()

    def paint_media(self, p, w, h):
        if not self.has_media():
            p.setPen(GRAY)
            p.setFont(self.face(15, QFont.Weight.Medium))
            msg = "Nothing playing" if HAS_MEDIA else "Media info needs Windows"
            p.drawText(QRectF(0, 0, w, h - 16), Qt.AlignmentFlag.AlignCenter, "\u266a  " + msg)
            return
        m, r = self.media, self.media_rects(w, h)
        self.draw_art(p, r["art"], 14)

        f1, f2 = self.face(16, QFont.Weight.DemiBold), self.face(13)
        sub = "  \u00b7  ".join(x for x in (m.get("artist"), m.get("app")) if x)
        left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        p.setPen(WHITE)
        p.setFont(f1)
        p.drawText(QRectF(r["tx"], 18, r["tw"], 22), left,
                   QFontMetrics(f1).elidedText(m.get("title") or "Unknown",
                                               Qt.TextElideMode.ElideRight, int(r["tw"])))
        p.setPen(GRAY)
        p.setFont(f2)
        p.drawText(QRectF(r["tx"], 40, r["tw"], 18), left,
                   QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, int(r["tw"])))

        self.draw_icon(p, "prev", r["prev"].center(), 18)
        self.draw_icon(p, "pause" if m.get("playing") else "play", r["toggle"].center(), 24)
        self.draw_icon(p, "next", r["next"].center(), 18)

        bar = r["bar"]
        p.setFont(self.face(11, QFont.Weight.Medium))
        if m.get("dur", 0) > 0:
            frac = self.cur_pos() / m["dur"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(WHITE)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(4, bar.width() * frac), bar.height()), 2, 2)
            p.setPen(GRAY)
            p.drawText(QRectF(10, bar.y() - 8, 46, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_time(self.cur_pos()))
            p.drawText(QRectF(bar.right() + 6, bar.y() - 8, 50, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fmt_time(m["dur"]))
        else:
            p.setPen(GRAY)
            p.drawText(QRectF(0, bar.y() - 8, w, 20), Qt.AlignmentFlag.AlignCenter, "LIVE")

    def paint_timer(self, p, w, h):
        r = self.timer_rects(w, h)
        center = Qt.AlignmentFlag.AlignCenter
        flash = self.alarm and int(time.monotonic() * 2) % 2 == 0
        if self.alarm:
            p.setPen(ACCENT)
            p.setFont(self.face(13, QFont.Weight.DemiBold))
            p.drawText(QRectF(0, 8, w, 22), center, "Time is up")
        else:
            p.setFont(self.face(12, QFont.Weight.Medium))
            for key, label, mode in (("tab_timer", "Timer", "timer"), ("tab_watch", "Stopwatch", "watch")):
                active = self.mode == mode
                if active:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 30))
                    p.drawRoundedRect(r[key], 11, 11)
                p.setPen(WHITE if active else GRAY)
                p.drawText(r[key], center, label)

        if self.mode == "timer":
            text = fmt_time(math.ceil(self.cd.remaining()))
        else:
            e = self.sw.elapsed()
            text = f"{fmt_time(e)}.{int(e * 10) % 10}"
        p.setFont(self.face(40, QFont.Weight.Light))
        p.setPen(ACCENT if flash else WHITE)
        p.drawText(QRectF(0, 34, w, 54), center, text)

        if self.mode == "timer" and not self.alarm:
            p.setFont(self.face(12, QFont.Weight.Medium))
            for key, label in (("minus", "-1m"), ("plus", "+1m")):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 26))
                p.drawRoundedRect(r[key], 13, 13)
                p.setPen(WHITE)
                p.drawText(r[key], center, label)

        running = (self.cd if self.mode == "timer" else self.sw).running
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawEllipse(r["reset"])
        p.drawEllipse(r["toggle"])
        self.draw_icon(p, "stop", r["reset"].center(), 12)
        self.draw_icon(p, "pause" if running else "play", r["toggle"].center(), 14)

    def paint_calendar(self, p, w, h):
        today = dt.date.today()
        first = 6 if WEEK_STARTS_SUNDAY else 0
        weeks = calendar.Calendar(firstweekday=first).monthdayscalendar(today.year, today.month)
        center = Qt.AlignmentFlag.AlignCenter
        p.setPen(WHITE)
        p.setFont(self.face(15, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, 10, w, 24), center, f"{today:%B %Y}")

        col = (w - 48) / 7
        p.setPen(GRAY)
        p.setFont(self.face(11, QFont.Weight.Medium))
        letters = "SMTWTFS" if WEEK_STARTS_SUNDAY else "MTWTFSS"
        for i, ch in enumerate(letters):
            p.drawText(QRectF(24 + i * col, 38, col, 16), center, ch)

        p.setFont(self.face(12, QFont.Weight.Medium))
        for row, week in enumerate(weeks):
            for c, day in enumerate(week):
                if not day:
                    continue
                cell = QRectF(24 + c * col, 58 + row * 22, col, 22)
                if day == today.day:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(WHITE)
                    p.drawEllipse(cell.center(), 10, 10)
                    p.setPen(BLACK)
                else:
                    p.setPen(WHITE if day > today.day else QColor(255, 255, 255, 105))
                p.drawText(cell, center, str(day))

    def paint_system(self, p, w, h):
        s = self.stats
        batt = s["batt"]
        if batt is None:
            bval, btxt, bcol, bcap = 100, "AC", GREEN, "Power"
        else:
            bcol = GREEN if s["plugged"] else RED if batt <= 20 else AMBER if batt <= 40 else GREEN
            bval, btxt, bcap = batt, f"{batt:.0f}%", "Charging" if s["plugged"] else "Battery"
        items = [(bcap, bval, bcol, btxt),
                 ("CPU", s["cpu"], BLUE, f"{s['cpu']:.0f}%"),
                 ("Memory", s["ram"], PURPLE, f"{s['ram']:.0f}%")]
        d, cy = 54, 44
        for i, (name, val, col, txt) in enumerate(items):
            cx = w * (2 * i + 1) / 6
            ring = QRectF(cx - d / 2 + 3, cy - d / 2 + 3, d - 6, d - 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 34), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(ring)
            if val > 0:
                p.setPen(QPen(col, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(ring, 90 * 16, int(-360 * 16 * min(val, 100) / 100))
            p.setPen(WHITE)
            p.setFont(self.face(13, QFont.Weight.DemiBold))
            p.drawText(QRectF(cx - d / 2, cy - d / 2, d, d), Qt.AlignmentFlag.AlignCenter, txt)
            p.setPen(GRAY)
            p.setFont(self.face(11))
            p.drawText(QRectF(cx - 40, cy + d / 2 + 4, 80, 16), Qt.AlignmentFlag.AlignCenter, name)

    def paint_dots(self, p, w, h):
        p.setPen(Qt.PenStyle.NoPen)
        mid = (PAGE_COUNT - 1) / 2
        for i in range(PAGE_COUNT):
            p.setBrush(WHITE if i == self.page else QColor(255, 255, 255, 60))
            p.drawEllipse(QPointF(w / 2 + (i - mid) * 11, h - 9), 2.4, 2.4)


def main():
    if already_running():
        return
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PandaIsland.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(resource("icon.png"))
    app.setWindowIcon(icon)
    island = Island(icon)
    island.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
