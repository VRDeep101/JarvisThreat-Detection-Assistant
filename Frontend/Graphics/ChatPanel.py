# =============================================================
#  Frontend/Graphics/ChatPanel.py - Holographic Chat
#
#  Kya karta:
#    - Scrollable chat display
#    - User + Jarvis message bubbles (different styles)
#    - Typewriter effect for Jarvis messages (letter-by-letter)
#    - Theme-aware colors
#    - Auto-scroll to bottom on new message
#    - Fade-in animation for new messages
#    - TEXT INPUT at bottom (type or press Enter to send)  ← NEW
#
#  Usage:
#    chat = ChatPanel(theme=neural_theme)
#    chat.add_user("hello jarvis")
#    chat.add_jarvis("At your service, Sir.")
#    chat.text_submitted.connect(my_handler)  ← connect this in GUI.py
# =============================================================

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QSizePolicy, QGraphicsOpacityEffect, QLineEdit, QPushButton,
)

from Frontend.Themes.base_theme import Theme
from Frontend.Themes.neural_theme import neural_theme


# =============================================================
#  Single message bubble
# =============================================================
class MessageBubble(QFrame):
    """One chat message (user or Jarvis)."""

    def __init__(self, text: str, is_user: bool, theme: Theme, typewriter: bool = False, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.is_user = is_user
        self.full_text = text
        self._timer = None  # keep ref to avoid GC

        self.setObjectName("bubble")
        self._apply_style()

        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        # Label
        self.label = QLabel("" if typewriter else text)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.label.setStyleSheet(f"""
            color: {theme.text_primary};
            background: transparent;
            border: none;
            font-family: "{theme.font_main}";
            font-size: 10pt;
        """)
        layout.addWidget(self.label)

        # Typewriter effect for Jarvis messages only
        self._typewriter_idx = 0
        if typewriter and not is_user:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick_typewriter)
            self._timer.start(18)  # ms per character

        # Fade-in animation
        self._apply_fade_in()

    def _apply_style(self):
        t = self.theme
        if self.is_user:
            self.setStyleSheet(f"""
                QFrame#bubble {{
                    background-color: {t.hex_with_alpha(t.primary, 0.08)};
                    border: 1px solid {t.hex_with_alpha(t.primary, 0.3)};
                    border-radius: 10px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame#bubble {{
                    background-color: {t.bg_panel};
                    border: 1px solid {t.hex_with_alpha(t.primary, 0.5)};
                    border-radius: 10px;
                }}
            """)

    def _tick_typewriter(self):
        if self._typewriter_idx >= len(self.full_text):
            if self._timer:
                self._timer.stop()
            return
        self._typewriter_idx += 1
        self.label.setText(self.full_text[:self._typewriter_idx])

    def _apply_fade_in(self):
        self.opacity_effect = QGraphicsOpacityEffect()
        self.opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity_effect)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(350)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.fade_anim.start()


# =============================================================
#  Chat container
# =============================================================
class ChatPanel(QWidget):
    """Scrollable holographic chat with text input at the bottom."""

    message_added  = pyqtSignal(str, bool)  # (text, is_user)  — informational
    text_submitted = pyqtSignal(str)         # emitted when user types + hits Enter

    def __init__(self, theme: Theme = None, parent=None):
        super().__init__(parent)
        self.theme = theme or neural_theme
        self._setup_ui()
        self.setMinimumWidth(350)

    # =========================================================
    #  UI BUILD
    # =========================================================
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ─────────────────────────────────────────────
        self.header = QLabel("CHAT")
        self.header.setStyleSheet(f"""
            color: {self.theme.primary};
            font-family: "{self.theme.font_display}";
            font-size: 10pt;
            font-weight: bold;
            letter-spacing: 3px;
            padding: 8px 12px;
            border-bottom: 1px solid {self.theme.border};
        """)
        layout.addWidget(self.header)

        # ── Scrollable message area ─────────────────────────────
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {self.theme.bg_panel};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {self.theme.hex_with_alpha(self.theme.primary, 0.5)};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                border: none; background: none; height: 0;
            }}
        """)

        # Content widget inside scroll
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(10)
        self.content_layout.addStretch()   # push bubbles to bottom

        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, stretch=1)

        # ── Text input area ─────────────────────────────────────
        input_container = QFrame()
        input_container.setStyleSheet(f"""
            QFrame {{
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.04)};
                border-top: 1px solid {self.theme.border};
            }}
        """)
        input_row = QHBoxLayout(input_container)
        input_row.setContentsMargins(10, 8, 10, 8)
        input_row.setSpacing(8)

        # Mic indicator label (non-functional decoration)
        self.mic_label = QLabel("⌨")
        self.mic_label.setFixedWidth(22)
        self.mic_label.setStyleSheet(f"""
            color: {self.theme.hex_with_alpha(self.theme.primary, 0.5)};
            font-size: 13pt;
            background: transparent;
        """)
        input_row.addWidget(self.mic_label)

        # The actual text input field
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Type a command or use voice...")
        self.text_input.setMinimumHeight(38)
        self.text_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.08)};
                border: 1px solid {self.theme.hex_with_alpha(self.theme.primary, 0.25)};
                border-radius: 6px;
                color: {self.theme.text_primary};
                padding: 6px 12px;
                font-family: "{self.theme.font_main}";
                font-size: 10pt;
                selection-background-color: {self.theme.primary};
            }}
            QLineEdit:focus {{
                border: 1px solid {self.theme.hex_with_alpha(self.theme.primary, 0.75)};
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.12)};
            }}
        """)
        self.text_input.returnPressed.connect(self._on_text_submitted)
        input_row.addWidget(self.text_input, stretch=1)

        # Send button
        self.send_btn = QPushButton("→")
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.15)};
                border: 1px solid {self.theme.hex_with_alpha(self.theme.primary, 0.4)};
                border-radius: 6px;
                color: {self.theme.primary};
                font-size: 14pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.3)};
            }}
            QPushButton:pressed {{
                background-color: {self.theme.hex_with_alpha(self.theme.primary, 0.5)};
            }}
        """)
        self.send_btn.clicked.connect(self._on_text_submitted)
        input_row.addWidget(self.send_btn)

        layout.addWidget(input_container)

    # =========================================================
    #  Slot: user hit Enter or clicked → button
    # =========================================================
    def _on_text_submitted(self):
        """Called when user presses Enter or clicks the send button."""
        text = self.text_input.text().strip()
        if not text:
            return
        # Show bubble in chat panel immediately (voice path adds via sig_user_msg)
        self.add_user(text)
        # Emit signal — Main.py connects this to _process_command
        self.text_submitted.emit(text)
        # Clear field
        self.text_input.clear()
        # Re-focus so user can type next command without clicking
        self.text_input.setFocus()

    # =========================================================
    #  Public API
    # =========================================================
    def add_user(self, text: str):
        """Add a user bubble (right-aligned, no typewriter)."""
        self._add_bubble(text, is_user=True, typewriter=False)
        self.message_added.emit(text, True)

    def add_jarvis(self, text: str, typewriter: bool = True):
        """Add a Jarvis response bubble (left-aligned, typewriter optional)."""
        self._add_bubble(text, is_user=False, typewriter=typewriter)
        self.message_added.emit(text, False)

    def clear(self):
        """Remove all message bubbles."""
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def focus_input(self):
        """Programmatically focus the text input field."""
        self.text_input.setFocus()

    def set_input_enabled(self, enabled: bool):
        """Enable or disable the text input (e.g., disable while JARVIS speaks)."""
        self.text_input.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)

    def set_theme(self, theme: Theme):
        """Hot-swap theme (called when mode switches)."""
        self.theme = theme
        self.header.setStyleSheet(f"""
            color: {theme.primary};
            font-family: "{theme.font_display}";
            font-size: 10pt;
            font-weight: bold;
            letter-spacing: 3px;
            padding: 8px 12px;
            border-bottom: 1px solid {theme.border};
        """)
        self.text_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.hex_with_alpha(theme.primary, 0.08)};
                border: 1px solid {theme.hex_with_alpha(theme.primary, 0.25)};
                border-radius: 6px;
                color: {theme.text_primary};
                padding: 6px 12px;
                font-family: "{theme.font_main}";
                font-size: 10pt;
                selection-background-color: {theme.primary};
            }}
            QLineEdit:focus {{
                border: 1px solid {theme.hex_with_alpha(theme.primary, 0.75)};
                background-color: {theme.hex_with_alpha(theme.primary, 0.12)};
            }}
        """)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.hex_with_alpha(theme.primary, 0.15)};
                border: 1px solid {theme.hex_with_alpha(theme.primary, 0.4)};
                border-radius: 6px;
                color: {theme.primary};
                font-size: 14pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {theme.hex_with_alpha(theme.primary, 0.3)};
            }}
            QPushButton:pressed {{
                background-color: {theme.hex_with_alpha(theme.primary, 0.5)};
            }}
        """)

    # =========================================================
    #  Internals
    # =========================================================
    def _add_bubble(self, text: str, is_user: bool, typewriter: bool):
        bubble = MessageBubble(
            text, is_user=is_user, theme=self.theme, typewriter=typewriter
        )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        if is_user:
            row.addStretch()
            row.addWidget(bubble, stretch=0)
        else:
            row.addWidget(bubble, stretch=0)
            row.addStretch()

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_widget.setLayout(row)

        # Insert above the trailing stretch item
        count = self.content_layout.count()
        self.content_layout.insertWidget(count - 1, row_widget)

        # Scroll to latest message after paint
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        scrollbar = self.scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


# =============================================================
#  Quick standalone test — python Frontend/Graphics/ChatPanel.py
# =============================================================
if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("ChatPanel — Text Input Test")
    win.resize(500, 720)
    win.setStyleSheet("background-color: #060B14;")

    chat = ChatPanel(theme=neural_theme)

    # Wire text_submitted to echo back as Jarvis
    def on_text(text: str):
        QTimer.singleShot(400, lambda: chat.add_jarvis(f"You said: «{text}»"))

    chat.text_submitted.connect(on_text)

    # Demo messages
    chat.add_user("hello jarvis")
    chat.add_jarvis("At your service, Sir. How can I help?")
    chat.add_user("what's the weather in pune")
    chat.add_jarvis("Currently 28°C in Pune, Sir. Partly cloudy.")

    win.setCentralWidget(chat)
    win.show()

    sys.exit(app.exec_())