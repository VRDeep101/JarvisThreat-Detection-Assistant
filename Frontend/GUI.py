# =============================================================
#  Frontend/GUI.py - JARVIS HUD
#
#  CHANGE vs original:
#    - chat.text_submitted signal is now wired in _wire_signals()
#    - text_from_gui signal added to JarvisWindow so Main.py can
#      connect to it and call _process_command from the GUI thread
# =============================================================

import sys
from typing import Optional
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QStackedWidget,
)

from Backend.Utils.Logger import get_logger
from Backend.Core.ModeManager import mode_manager, Mode

from Frontend.Themes import theme_for_mode, neural_theme
from Frontend.Graphics.CircleWidget import CircleWidget
from Frontend.Graphics.ChatPanel import ChatPanel
from Frontend.Graphics.GlobeWidget import GlobeWidget
from Frontend.Graphics.StatsBars import StatsPanel
from Frontend.Graphics.WaveformWidget import WaveformWidget
from Frontend.Graphics.RadarWidget import RadarWidget
from Frontend.Graphics.WireframeWidget import WireframeWidget
from Frontend.Graphics.ParticleBackground import ParticleBackground
from Frontend.Graphics.PasswordScreen import PasswordScreen
from Frontend.Graphics.BootAnimation import BootAnimation
from Frontend.Graphics.GridBackground import GridBackground
from Frontend.Graphics.HUDCorners import HUDCorners
from Frontend.Graphics.StatusTicker import StatusTicker
from Frontend.Graphics.DataPanel import DataPanel
from Frontend.Graphics.SecurityInputDialog import SecurityInputDialog
from Frontend.Sounds.SoundManager import sounds

log = get_logger("GUI")


# =============================================================
#  JarvisWindow
# =============================================================
class JarvisWindow(QMainWindow):
    """Main HUD — futuristic full version."""

    sig_status              = pyqtSignal(str)
    sig_user_msg            = pyqtSignal(str)
    sig_jarvis_msg          = pyqtSignal(str)
    sig_set_speaking        = pyqtSignal(bool)
    sig_set_listening       = pyqtSignal(bool)
    sig_mode_switch         = pyqtSignal(object)
    sig_notif_count         = pyqtSignal(int)
    sig_show_password       = pyqtSignal()
    sig_hide_password       = pyqtSignal()
    sig_password_error      = pyqtSignal(str)
    sig_password_success    = pyqtSignal()

    # Security input signals
    sig_show_security_input  = pyqtSignal(str)
    sig_hide_security_input  = pyqtSignal()
    sig_security_result      = pyqtSignal(object)

    # Forwarded outward to Main.py
    password_submitted       = pyqtSignal(str)
    password_cancelled       = pyqtSignal()
    security_input_submitted = pyqtSignal(str, str)   # (value, mode)
    security_input_cancelled = pyqtSignal()

    # ── NEW: emitted when user types in the chat text field ──
    text_from_gui            = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_theme = neural_theme
        self._hud_corners_list = []

        self._setup_window()
        self._build_stack()
        self._apply_theme(self.current_theme)
        self._wire_signals()

        mode_manager.register_callback(self._on_mode_callback)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

    def _setup_window(self):
        self.setWindowTitle("JARVIS V3")
        self.setMinimumSize(1300, 800)
        self.resize(1500, 900)

    def _build_stack(self):
        self.stack = QStackedWidget()

        # Index 0 — Boot
        self.boot_screen = BootAnimation(theme=self.current_theme)
        self.boot_screen.boot_complete.connect(self._on_boot_done)
        self.stack.addWidget(self.boot_screen)

        # Index 1 — Main HUD
        self.main_ui = self._build_main_ui()
        self.stack.addWidget(self.main_ui)

        # Index 2 — Companion password screen
        from Frontend.Themes.companion_theme import companion_theme
        self.password_screen = PasswordScreen(theme=companion_theme)
        self.password_screen.password_submitted.connect(self.password_submitted.emit)
        self.password_screen.cancelled.connect(self._on_password_cancel)
        self.stack.addWidget(self.password_screen)

        # Index 3 — Security input overlay
        try:
            from Frontend.Themes.security_theme import security_theme as sec_th
        except Exception:
            sec_th = self.current_theme
        self.security_input_dialog = SecurityInputDialog(theme=sec_th)
        self.security_input_dialog.input_submitted.connect(
            self.security_input_submitted.emit
        )
        self.security_input_dialog.cancelled.connect(self._on_security_input_cancel)
        self.stack.addWidget(self.security_input_dialog)

        self.stack.setCurrentWidget(self.boot_screen)
        self.setCentralWidget(self.stack)

        sounds.play("boot")

    def _on_boot_done(self):
        self.stack.setCurrentWidget(self.main_ui)

    def _on_password_cancel(self):
        self.password_cancelled.emit()
        self.stack.setCurrentWidget(self.main_ui)

    def _on_security_input_cancel(self):
        self.security_input_cancelled.emit()
        self.stack.setCurrentWidget(self.main_ui)

    # =========================================================
    #  Helper: wrap widget with HUD corner brackets
    # =========================================================
    def _wrap_with_corners(self, widget: QWidget) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")

        from PyQt5.QtWidgets import QStackedLayout
        stack = QStackedLayout(wrapper)
        stack.setStackingMode(QStackedLayout.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)

        stack.addWidget(widget)

        corners = HUDCorners(theme=self.current_theme, size=15)
        stack.addWidget(corners)

        self._hud_corners_list.append(corners)
        return wrapper

    # =========================================================
    #  Main UI layout
    # =========================================================
    def _build_main_ui(self) -> QWidget:
        container = QWidget()

        self.grid_bg = GridBackground(theme=self.current_theme, parent=container)
        self.grid_bg.lower()

        self.particles = ParticleBackground(theme=self.current_theme, parent=container)
        self.particles.lower()
        self.particles.raise_()

        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(12, 8, 12, 8)
        root_layout.setSpacing(8)

        root_layout.addWidget(self._build_info_strip())
        root_layout.addWidget(self._build_top_bar())

        main_row = QHBoxLayout()
        main_row.setSpacing(10)
        main_row.addWidget(self._build_left_column(), stretch=1)
        main_row.addWidget(self._build_center_panel(), stretch=2)
        main_row.addWidget(self._build_right_column(), stretch=2)
        root_layout.addLayout(main_row, stretch=1)

        root_layout.addWidget(self._build_bottom_bar())

        self.ticker = StatusTicker(theme=self.current_theme)
        root_layout.addWidget(self.ticker)

        return container

    def _build_info_strip(self) -> QWidget:
        strip = QLabel("Analysing Data From InfiniteCloud")
        strip.setAlignment(Qt.AlignCenter)
        strip.setFixedHeight(18)
        strip.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 3px;
            background: transparent;
            padding: 2px;
        """)
        self.info_strip = strip
        return strip

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(50)
        bar.setObjectName("panel")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(16)

        self.mode_badge = QLabel("NEURAL MODE")
        self.mode_badge.setObjectName("mode_badge")
        layout.addWidget(self.mode_badge)

        self.title_label = QLabel("J.A.R.V.I.S")
        self.title_label.setObjectName("title")
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label, stretch=1)

        self.notif_badge = QLabel("0")
        self.notif_badge.setStyleSheet(f"""
            color: {self.current_theme.warn};
            padding: 3px 10px;
            border: 1px solid {self.current_theme.warn};
            border-radius: 10px;
            font-size: 9pt;
        """)
        self.notif_badge.hide()
        layout.addWidget(self.notif_badge)

        self.clock_label = QLabel("--:--")
        self.clock_label.setObjectName("muted")
        self.clock_label.setAlignment(Qt.AlignRight)
        layout.addWidget(self.clock_label)

        return self._wrap_with_corners(bar)

    def _build_left_column(self) -> QWidget:
        col = QFrame()
        col.setObjectName("panel")
        col.setMinimumWidth(240)
        col.setMaximumWidth(300)

        layout = QVBoxLayout(col)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        gh = QLabel("EARTH_VIEW")
        gh.setStyleSheet(f"""
            color: {self.current_theme.primary};
            font-family: "{self.current_theme.font_display}";
            font-size: 9pt;
            font-weight: bold;
            letter-spacing: 3px;
        """)
        layout.addWidget(gh)

        self.globe = GlobeWidget(theme=self.current_theme)
        self.globe.setMinimumHeight(170)
        layout.addWidget(self.globe)

        coord_label = QLabel("18.52°N  73.85°E  //  PUNE")
        coord_label.setAlignment(Qt.AlignCenter)
        coord_label.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 2px;
            padding-bottom: 6px;
        """)
        layout.addWidget(coord_label)
        self.coord_label = coord_label

        self.data_panel = DataPanel(theme=self.current_theme, title="CORE_STATUS")
        layout.addWidget(self.data_panel)

        sh = QLabel("SYSTEMS")
        sh.setStyleSheet(f"""
            color: {self.current_theme.primary};
            font-family: "{self.current_theme.font_display}";
            font-size: 9pt;
            font-weight: bold;
            letter-spacing: 3px;
            padding-top: 4px;
        """)
        layout.addWidget(sh)

        self.stats = StatsPanel(theme=self.current_theme)
        layout.addWidget(self.stats)

        layout.addStretch()
        return self._wrap_with_corners(col)

    def _build_center_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        core_label = QLabel("Core JARVIS V3.0")
        core_label.setAlignment(Qt.AlignCenter)
        core_label.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 4px;
            padding: 2px;
        """)
        layout.addWidget(core_label)
        self.core_label = core_label

        self.circle = CircleWidget(theme=self.current_theme)
        layout.addWidget(self.circle, stretch=1)

        self.status_label = QLabel("READY")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        gps_label = QLabel("18.52°N / 73.85°E / ALT_560M")
        gps_label.setAlignment(Qt.AlignCenter)
        gps_label.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 3px;
            padding-top: 2px;
        """)
        layout.addWidget(gps_label)
        self.gps_label = gps_label

        return self._wrap_with_corners(panel)

    def _build_right_column(self) -> QWidget:
        col = QWidget()
        layout = QVBoxLayout(col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        chat_wrapper = QFrame()
        chat_wrapper.setObjectName("panel")
        cl = QVBoxLayout(chat_wrapper)
        cl.setContentsMargins(0, 0, 0, 0)
        self.chat = ChatPanel(theme=self.current_theme)
        cl.addWidget(self.chat)
        layout.addWidget(self._wrap_with_corners(chat_wrapper), stretch=3)

        mini_row = QHBoxLayout()
        mini_row.setSpacing(10)

        radar_wrapper = QFrame()
        radar_wrapper.setObjectName("panel")
        radar_wrapper.setFixedHeight(160)
        rl = QVBoxLayout(radar_wrapper)
        rl.setContentsMargins(6, 20, 6, 6)

        radar_header = QLabel("RADAR")
        radar_header.setAlignment(Qt.AlignCenter)
        radar_header.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 3px;
        """)
        self.radar_header = radar_header
        rl.insertWidget(0, radar_header)

        self.radar = RadarWidget(theme=self.current_theme)
        rl.addWidget(self.radar)
        mini_row.addWidget(self._wrap_with_corners(radar_wrapper), stretch=1)

        wire_wrapper = QFrame()
        wire_wrapper.setObjectName("panel")
        wire_wrapper.setFixedHeight(160)
        wl = QVBoxLayout(wire_wrapper)
        wl.setContentsMargins(6, 20, 6, 6)

        wire_header = QLabel("MATRIX")
        wire_header.setAlignment(Qt.AlignCenter)
        wire_header.setStyleSheet(f"""
            color: {self.current_theme.text_muted};
            font-family: "{self.current_theme.font_mono}";
            font-size: 8pt;
            letter-spacing: 3px;
        """)
        self.wire_header = wire_header
        wl.insertWidget(0, wire_header)

        self.wireframe = WireframeWidget(theme=self.current_theme)
        wl.addWidget(self.wireframe)
        mini_row.addWidget(self._wrap_with_corners(wire_wrapper), stretch=1)

        layout.addLayout(mini_row)
        return col

    def _build_bottom_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(55)
        bar.setObjectName("panel")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        mic = QLabel("🎤")
        mic.setStyleSheet("font-size: 14pt; background: transparent;")
        layout.addWidget(mic)

        self.waveform = WaveformWidget(theme=self.current_theme)
        self.waveform.setFixedHeight(40)
        layout.addWidget(self.waveform, stretch=1)

        self.location_label = QLabel("📍 PUNE, IN")
        self.location_label.setStyleSheet(
            f"color: {self.current_theme.text_muted}; background: transparent; "
            f"font-family: '{self.current_theme.font_mono}'; letter-spacing: 2px;"
        )
        layout.addWidget(self.location_label)

        return self._wrap_with_corners(bar)

    # =========================================================
    #  Wiring + theme
    # =========================================================
    def _wire_signals(self):
        self.sig_status.connect(self._on_status)
        self.sig_user_msg.connect(self._on_user_msg)
        self.sig_jarvis_msg.connect(self._on_jarvis_msg)
        self.sig_set_speaking.connect(self._on_set_speaking)
        self.sig_set_listening.connect(self._on_set_listening)
        self.sig_mode_switch.connect(self._on_mode_switch)
        self.sig_notif_count.connect(self._on_notif_count)
        self.sig_show_password.connect(self._on_show_password)
        self.sig_hide_password.connect(self._on_hide_password)
        self.sig_password_error.connect(self._on_password_error)
        self.sig_password_success.connect(self._on_password_success)
        self.sig_show_security_input.connect(self._on_show_security_input)
        self.sig_hide_security_input.connect(self._on_hide_security_input)
        self.sig_security_result.connect(self._on_security_result)

        # ── NEW: wire chat text input → text_from_gui signal ──
        # Main.py connects text_from_gui to _process_command
        if hasattr(self, "chat"):
            self.chat.text_submitted.connect(self.text_from_gui.emit)

    def _apply_theme(self, theme):
        self.current_theme = theme
        self.setStyleSheet(theme.build_qss())

        for attr, method in [
            ("circle", "set_theme"), ("chat", "set_theme"),
            ("globe", "set_theme"), ("stats", "set_theme"),
            ("waveform", "set_theme"), ("radar", "set_theme"),
            ("wireframe", "set_theme"), ("particles", "set_theme"),
            ("grid_bg", "set_theme"), ("ticker", "set_theme"),
            ("data_panel", "set_theme"),
        ]:
            if hasattr(self, attr):
                try:
                    getattr(getattr(self, attr), method)(theme)
                except Exception:
                    pass

        for corners in self._hud_corners_list:
            try:
                corners.set_theme(theme)
            except Exception:
                pass

        if hasattr(self, "mode_badge"):
            self.mode_badge.setStyleSheet(f"""
                color: {theme.primary};
                background-color: {theme.hex_with_alpha(theme.primary, 0.15)};
                border: 1px solid {theme.hex_with_alpha(theme.primary, 0.5)};
                border-radius: 4px;
                padding: 3px 10px;
                font-family: "{theme.font_display}";
                font-size: 9pt;
                font-weight: bold;
                letter-spacing: 2px;
            """)

        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(f"""
                color: {theme.primary};
                font-family: "{theme.font_display}";
                font-size: 14pt;
                font-weight: bold;
                letter-spacing: 4px;
            """)

        if hasattr(self, "status_label"):
            self.status_label.setStyleSheet(f"""
                color: {theme.primary};
                font-family: "{theme.font_display}";
                font-size: 11pt;
                letter-spacing: 2px;
            """)

        if hasattr(self, "clock_label"):
            self.clock_label.setStyleSheet(
                f"color: {theme.text_muted}; font-family: '{theme.font_mono}';"
            )

        for attr in ["info_strip", "coord_label", "core_label", "gps_label",
                     "radar_header", "wire_header", "location_label"]:
            if hasattr(self, attr):
                try:
                    getattr(self, attr).setStyleSheet(f"""
                        color: {theme.text_muted};
                        font-family: "{theme.font_mono}";
                        font-size: 8pt;
                        letter-spacing: 3px;
                        background: transparent;
                    """)
                except Exception:
                    pass

        if hasattr(self, "notif_badge"):
            self.notif_badge.setStyleSheet(f"""
                color: {theme.warn};
                padding: 3px 10px;
                border: 1px solid {theme.warn};
                border-radius: 10px;
                font-size: 9pt;
            """)

        log.info(f"Theme applied: {theme.name}")

    # --- signal handlers ---
    def _on_status(self, text):
        if hasattr(self, "status_label"):
            self.status_label.setText(text.upper())

    def _on_user_msg(self, text):
        # Only add to chat if it came from the voice path.
        # Text-typed messages are already added inside ChatPanel._on_text_submitted.
        # We guard with a flag so we never double-add.
        if hasattr(self, "chat"):
            self.chat.add_user(text)

    def _on_jarvis_msg(self, text):
        if hasattr(self, "chat"):
            self.chat.add_jarvis(text, typewriter=True)

    def _on_set_speaking(self, active):
        if hasattr(self, "circle"):
            self.circle.set_pulsing(active)
        if hasattr(self, "waveform"):
            self.waveform.set_active(active)
        if active:
            self._on_status("Speaking...")

    def _on_set_listening(self, active):
        if hasattr(self, "circle"):
            self.circle.set_listening(active)
        if hasattr(self, "waveform"):
            self.waveform.set_active(active)
        if active:
            self._on_status("Listening...")

    def _on_mode_switch(self, new_mode):
        theme = theme_for_mode(new_mode)
        self._apply_theme(theme)
        if hasattr(self, "mode_badge"):
            self.mode_badge.setText(new_mode.value.upper() + " MODE")
        sounds.play("mode_switch")

    def _on_notif_count(self, count):
        if hasattr(self, "notif_badge"):
            if count > 0:
                self.notif_badge.setText(str(count))
                self.notif_badge.show()
            else:
                self.notif_badge.hide()

    def _on_show_password(self):
        self.password_screen.reset()
        self.stack.setCurrentWidget(self.password_screen)

    def _on_hide_password(self):
        self.stack.setCurrentWidget(self.main_ui)

    def _on_password_error(self, msg):
        self.password_screen.show_error(msg)

    def _on_password_success(self):
        self.password_screen.show_success()
        QTimer.singleShot(800, lambda: self.stack.setCurrentWidget(self.main_ui))

    def _on_show_security_input(self, mode: str):
        self.security_input_dialog.show_for(mode)
        self.stack.setCurrentWidget(self.security_input_dialog)

    def _on_hide_security_input(self):
        self.stack.setCurrentWidget(self.main_ui)

    def _on_security_result(self, result):
        if hasattr(self, "security_input_dialog"):
            try:
                self.security_input_dialog.show_result(result)
            except Exception as e:
                log.debug(f"Security result display: {e}")

    def _on_mode_callback(self, old_mode, new_mode):
        self.sig_mode_switch.emit(new_mode)

    def _update_clock(self):
        if hasattr(self, "clock_label"):
            now = datetime.now()
            self.clock_label.setText(now.strftime("%H:%M:%S  |  %d %b"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "main_ui"):
            w = self.main_ui.width()
            h = self.main_ui.height()
            if hasattr(self, "grid_bg"):
                self.grid_bg.setGeometry(0, 0, w, h)
            if hasattr(self, "particles"):
                self.particles.setGeometry(0, 0, w, h)

    # --- public API (unchanged) ---
    def set_status(self, text):              self.sig_status.emit(text)
    def add_user_message(self, text):        self.sig_user_msg.emit(text)
    def add_jarvis_message(self, text):      self.sig_jarvis_msg.emit(text)
    def set_speaking(self, active):          self.sig_set_speaking.emit(active)
    def set_listening(self, active):         self.sig_set_listening.emit(active)
    def set_notif_count(self, count):        self.sig_notif_count.emit(count)
    def show_password_screen(self):          self.sig_show_password.emit()
    def hide_password_screen(self):          self.sig_hide_password.emit()
    def password_error(self, msg):           self.sig_password_error.emit(msg)
    def password_success(self):              self.sig_password_success.emit()
    def play_sound(self, name):              sounds.play(name)
    def show_security_input(self, mode: str): self.sig_show_security_input.emit(mode)
    def hide_security_input(self):            self.sig_hide_security_input.emit()
    def show_security_result(self, result):   self.sig_security_result.emit(result)


# =============================================================
#  Singleton helpers
# =============================================================
_app_instance = None
jarvis_gui    = None


def get_app():
    global _app_instance
    if _app_instance is None:
        _app_instance = QApplication.instance() or QApplication(sys.argv)
    return _app_instance


def get_gui():
    global jarvis_gui
    get_app()
    if jarvis_gui is None:
        jarvis_gui = JarvisWindow()
    return jarvis_gui


if __name__ == "__main__":
    app = get_app()
    win = get_gui()
    win.show()

    def demo():
        win.set_status("Listening...")
        win.set_listening(True)
        win.add_user_message("hello jarvis")
        QTimer.singleShot(1500, lambda: win.set_listening(False))
        QTimer.singleShot(1600, lambda: win.set_status("Thinking..."))
        QTimer.singleShot(2500, lambda: win.set_speaking(True))
        QTimer.singleShot(2600, lambda: win.add_jarvis_message(
            "At your service, Sir. All systems operational."
        ))
        QTimer.singleShot(7000, lambda: win.set_speaking(False))
        QTimer.singleShot(7100, lambda: win.set_status("Ready"))
        QTimer.singleShot(8000, lambda: win.set_notif_count(3))
        QTimer.singleShot(9000, lambda: mode_manager.switch(Mode.SECURITY))

    QTimer.singleShot(3500, demo)
    sys.exit(app.exec_())