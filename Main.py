# =============================================================
#  Main.py - JARVIS V3 Orchestrator
#
#  Changes vs V2:
#    - Text input wired: GUI.text_from_gui → _process_command_from_gui
#    - _process_command_from_gui() added (runs in background thread)
#    - _listen_loop: voice path skips add_user_message (ChatPanel handles it)
#    - Version label updated to V3
#
#  Voice path:  STT → _listen_loop → add_user_message → _process_command
#  Text path:   ChatPanel → text_submitted → GUI.text_from_gui
#               → _process_command_from_gui (bg thread) → _process_command
# =============================================================

import sys
import time
import threading
import signal
import re
from datetime import datetime
from typing import Optional, Any

# UTF-8 fix for Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# =============================================================
#  Backend imports
# =============================================================
from Backend.Utils.Logger import get_logger
from Backend.Utils.PathResolver import paths
from Backend.Utils.InternetCheck import net

from Backend.Core.ErrorHandler import error_handler, safe_run, handle_error
from Backend.Core.BackgroundTaskManager import task_mgr
from Backend.Core.ModeManager import mode_manager, Mode
from Backend.Core.ContextManager import context
from Backend.Core.Router import router
from Backend.Core.SelfEditor import self_editor

from Backend.Brain.Chatbot import chatbot
from Backend.Brain.Memory import memory
from Backend.Brain.Eq import eq
from Backend.Brain.ContinuousLearner import continuous_learner
from Backend.Brain.PersonalDataExtractor import personal_data_extractor
from Backend.Brain.ProactiveCheckIn import proactive_checkin

from Backend.Voice.TextToSpeech import tts
from Backend.Voice.SpeechToText import stt
from Backend.Voice.LoadingPhrases import loading_phrases
from Backend.Voice.PronunciationFixer import correct_stt_text

from Backend.Automation.AppRegistry import app_registry
from Backend.Automation.SystemControl import system
from Backend.Automation.WebAutomator import web_ai
from Backend.Automation.SpotifyController import spotify
from Backend.Automation.WhatsAppEngine import whatsapp

from Backend.Modes.NeuralMode import neural_mode
from Backend.Modes.SecurityMode import security_mode
from Backend.Modes.ScanningMode import scanning_mode
from Backend.Modes.CompanionMode import companion_mode
from Backend.Modes.GamingMode import gaming_mode
from Backend.Modes.OfflineMode import offline_mode

from Backend.External.WeatherEngine import weather
from Backend.External.NewsEngine import news
from Backend.External.WolframSolver import wolfram
from Backend.External.RealtimeSearchEngine import rts
from Backend.External.ImageGenerator import image_gen
from Backend.External.PhishingDetector import phishing

from Backend.Notifications.NotificationManager import notif_mgr
from Backend.Notifications.WindowsNotifListener import notif_listener
from Backend.Notifications.StartupGreeter import greeter

# =============================================================
#  Frontend
# =============================================================
from Frontend.GUI import get_gui, get_app
from PyQt5.QtCore import QTimer

log = get_logger("Main")


# =============================================================
#  HELPERS - Defensive type conversion
# =============================================================
def _to_str(value: Any) -> str:
    """Convert any return value (tuple, str, None, etc.) to a clean string."""
    if value is None:
        return ""
    if isinstance(value, tuple):
        return _to_str(value[0]) if len(value) > 0 else ""
    if isinstance(value, list):
        return _to_str(value[0]) if len(value) > 0 else ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _safe_dict_get(result: Any, key: str, default: str = "") -> str:
    """Get a key from a possibly-dict result, falling back to default."""
    if isinstance(result, dict):
        v = result.get(key, default)
        return _to_str(v) if v is not None else default
    return default


# =============================================================
#  JARVIS CORE
# =============================================================
class JarvisCore:
    """Main orchestrator for JARVIS V3."""

    def __init__(self):
        self.gui                       = None
        self.running                   = False
        self.listen_thread: Optional[threading.Thread] = None
        self._awaiting_password        = False
        self._goodbye_said             = False
        self._previous_mode: Optional[Mode] = None
        # Security-input state — True while the SecurityInputDialog overlay is
        # open so the voice listen loop pauses during keyboard entry.
        self._awaiting_security_input  = False
        self._security_input_mode      = "url"   # "url" | "password" | "email"
        # Lock that serialises _process_command so voice + text paths never
        # race against each other and produce interleaved output.
        self._process_lock             = threading.Lock()

    # =========================================================
    #  STARTUP
    # =========================================================
    def startup(self):
        log.info("=" * 60)
        log.info("  JARVIS V3 STARTING UP")
        log.info("=" * 60)

        self.running = True

        # ------------------------------------------------------------------
        # IMMEDIATE (non-blocking) wiring — must happen before event loop
        # ------------------------------------------------------------------

        # Password dialog signals
        try:
            self.gui.password_submitted.connect(self._on_password_submitted)
            self.gui.password_cancelled.connect(self._on_password_cancelled)
        except Exception as e:
            log.debug(f"Password wire skip: {e}")

        # Security-input dialog signals
        try:
            self.gui.security_input_submitted.connect(self._on_security_input)
            self.gui.security_input_cancelled.connect(self._on_security_input_cancelled)
        except Exception as e:
            log.debug(f"Security input wire skip: {e}")

        # Text-input from ChatPanel  ← NEW in V3
        # GUI emits text_from_gui(str) when the user types + presses Enter.
        # We spawn a background thread so the Qt event loop is never blocked.
        try:
            self.gui.text_from_gui.connect(self._process_command_from_gui)
            log.info("Text input wired: GUI.text_from_gui → _process_command_from_gui")
        except Exception as e:
            log.debug(f"Text input wire skip: {e}")

        # TTS state callback → animates speaking indicator in GUI
        try:
            tts.register_state_callback(self._on_tts_state)
        except Exception as e:
            log.debug(f"TTS callback skip: {e}")

        # ------------------------------------------------------------------
        # DEFERRED heavy init via QTimer.singleShot(0, …)
        #
        # startup() runs synchronously BEFORE app.exec_() so any blocking call
        # (net.is_online can take 2-5s on slow networks, notif_listener.start
        # spawns OS threads, etc.) would freeze the GUI and hang the BootAnimation.
        #
        # QTimer.singleShot(0, …) queues the callable for the very first idle
        # tick of the Qt event loop — by which time the window is already painted.
        # ------------------------------------------------------------------
        QTimer.singleShot(0, self._deferred_startup)

        log.info("Startup dispatched (heavy init deferred — no boot hang).")

    # -----------------------------------------------------------------
    def _deferred_startup(self):
        """
        Runs on the Qt main thread on its first idle event after show().
        Creates the voice-listener thread and spawns the background-init
        thread for all blocking operations.
        """
        self.listen_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="MainListener",
        )

        # Post-boot greeting at 3.5s (synced with BootAnimation length)
        QTimer.singleShot(3500, self._post_boot_greeting)

        # Voice listener starts at 5.5s (greeting has had time to begin)
        QTimer.singleShot(5500, self.listen_thread.start)

        # All blocking operations run in a daemon background thread so the
        # BootAnimation and the rest of the GUI stay fully responsive.
        init_thread = threading.Thread(
            target=self._background_init,
            daemon=True,
            name="BackgroundInit",
        )
        init_thread.start()

    # -----------------------------------------------------------------
    def _background_init(self):
        """
        Daemon thread — all potentially-slow startup operations live here.
        Never accesses PyQt5 widgets directly; GUI updates go via QTimer
        or signals on the main thread.
        """
        # 1. Net check
        try:
            online = bool(net.is_online())
        except Exception:
            online = False
        log.info(f"Internet: {'ONLINE' if online else 'OFFLINE'}")

        # 2. Initial mode
        try:
            if not online:
                mode_manager.switch(Mode.OFFLINE)
                offline_mode.enter(on_speak=None)
            else:
                mode_manager.switch(Mode.NEURAL)
                neural_mode.enter(on_speak=None)
        except Exception as e:
            log.error(f"Initial mode error: {e}")

        # 3. Net state-change callback
        try:
            net.register_callback(self._on_net_change)
        except Exception as e:
            log.debug(f"Net callback skip: {e}")

        # 4. Notification listener
        if online:
            try:
                notif_listener.start(on_notif=self._on_new_notif)
            except Exception as e:
                log.debug(f"Notif listener skip: {e}")

        # 5. Proactive check-in
        try:
            proactive_checkin.start(on_speak=self.speak)
        except Exception as e:
            log.debug(f"Proactive check-in skip: {e}")

        log.info("Background init complete.")

    def _post_boot_greeting(self):
        """Iron Man-style greeting after boot."""
        try:
            unread = 0
            try:
                unread = int(notif_mgr.get_unread_count())
            except Exception:
                pass
            self.gui.set_notif_count(unread)

            greeting = _to_str(greeter.build(include_notifs=True))
            if not greeting:
                greeting = "At your service, Sir."
            log.info(f"Greeting: {greeting}")

            self.gui.add_jarvis_message(greeting)
            self.speak(greeting)
            self.gui.set_status("Say 'jarvis' + command  or  type below")
        except Exception as e:
            log.error(f"Greeting: {e}")

    # =========================================================
    #  TTS → GUI sync
    # =========================================================
    def _on_tts_state(self, speaking: bool):
        try:
            self.gui.set_speaking(bool(speaking))
            if not speaking:
                self.gui.set_status("Ready")
        except Exception as e:
            log.debug(f"TTS->GUI: {e}")

    def speak(self, text: Any):
        """Speak via TTS and mirror text to the GUI chat panel."""
        text = _to_str(text)
        if not text.strip():
            return
        try:
            self.gui.add_jarvis_message(text)
        except Exception:
            pass
        try:
            tts.say(text)
            try:
                tts._fire_state_callbacks(True)
                QTimer.singleShot(100, lambda: tts._fire_state_callbacks(False))
            except Exception:
                pass
        except Exception as e:
            log.error(f"Speak: {e}")

    # =========================================================
    #  NET STATE
    # =========================================================
    def _on_net_change(self, online: bool):
        try:
            online = bool(online)
            if online:
                if mode_manager.current_mode == Mode.OFFLINE:
                    offline_mode.exit(on_speak=self.speak)
                    target = self._previous_mode or Mode.NEURAL
                    mode_manager.switch(target)
                    self._enter_mode(target)
            else:
                if mode_manager.current_mode != Mode.OFFLINE:
                    self._previous_mode = mode_manager.current_mode
                    self._exit_current_mode()
                    mode_manager.switch(Mode.OFFLINE)
                    offline_mode.enter(on_speak=self.speak)
        except Exception as e:
            log.error(f"Net change: {e}")

    # =========================================================
    #  NOTIFICATIONS
    # =========================================================
    def _on_new_notif(self, notif: dict):
        try:
            unread = 0
            try:
                unread = int(notif_mgr.get_unread_count())
            except Exception:
                pass
            self.gui.set_notif_count(unread)

            try:
                self.gui.play_sound("notification")
            except Exception:
                pass

            if mode_manager.current_mode == Mode.NEURAL:
                app = _to_str(notif.get("app", ""))
                try:
                    watched = notif_mgr.is_watched(app)
                except Exception:
                    watched = False
                if watched:
                    sender = _to_str(notif.get("title", ""))
                    msg = (
                        f"New {app} from {sender}."
                        if sender
                        else f"New {app} notification."
                    )
                    try:
                        if not tts.is_speaking:
                            self.speak(msg)
                    except Exception:
                        self.speak(msg)
        except Exception as e:
            log.error(f"Notif: {e}")

    # =========================================================
    #  VOICE LISTEN LOOP
    # =========================================================
    def _listen_loop(self):
        """
        Runs in MainListener daemon thread.
        STT strips the wake word internally — we receive only the clean command.
        """
        log.info("Voice listen loop started.")

        while self.running:
            try:
                # Pause during overlay screens (user is typing)
                if self._awaiting_password or self._awaiting_security_input:
                    time.sleep(0.5)
                    continue

                try:
                    self.gui.set_listening(True)
                    self.gui.set_status("Listening...")
                except Exception:
                    pass

                raw_result = None
                try:
                    raw_result = stt.listen()
                except Exception as e:
                    log.debug(f"STT error: {e}")
                    time.sleep(0.5)
                    continue

                try:
                    self.gui.set_listening(False)
                except Exception:
                    pass

                command = _to_str(raw_result).strip()
                if not command or len(command) < 2:
                    continue

                log.info(f"VOICE COMMAND: '{command}'")

                # Show in GUI — voice path owns the user bubble
                try:
                    self.gui.add_user_message(command)
                except Exception as e:
                    log.debug(f"GUI msg: {e}")

                # Self-echo filter (ignores JARVIS's own TTS fed back through mic)
                try:
                    if context.is_self_echo(command):
                        log.debug(f"Self-echo filtered: '{command[:40]}'")
                        continue
                except Exception:
                    pass

                log.action(f"PROCESSING (voice): {command}")
                self._process_command(command)

            except Exception as e:
                log.error(f"Listen loop error: {e}")
                time.sleep(1)

    # =========================================================
    #  TEXT INPUT HANDLER  ← NEW IN V3
    # =========================================================
    def _process_command_from_gui(self, command: str):
        """
        Called on the Qt main thread when the user types in ChatPanel and
        presses Enter.  ChatPanel has already added the user bubble, so we
        must NOT call add_user_message() again (would cause a duplicate).

        We spawn a background thread so the Qt event loop is never blocked
        by API calls, Selenium automation, or TTS.
        """
        command = _to_str(command).strip()
        if not command:
            return

        log.action(f"PROCESSING (text input): {command}")

        t = threading.Thread(
            target=self._process_command,
            args=(command,),
            daemon=True,
            name="TextInputProcessor",
        )
        t.start()

    # =========================================================
    #  COMMAND PROCESSING  (shared by voice + text paths)
    # =========================================================
    def _process_command(self, command: str):
        """
        Core pipeline.  Called with a clean command string.

        Voice path:     _listen_loop calls this directly (already in bg thread).
        Text path:      _process_command_from_gui spawns a thread → calls this.

        NOTE: The GUI user-bubble is added by the *caller* in both cases.
              Never call self.gui.add_user_message() inside this method.
        """
        command = _to_str(command).strip()
        if not command:
            return

        # Serialise so a fast typist can't race the voice path
        with self._process_lock:
            log.action(f"User: {command}")

            try:
                self.gui.set_status("Thinking...")

                # Context + activity
                try:
                    context.add_user(command)
                except Exception:
                    pass
                try:
                    proactive_checkin.register_activity()
                except Exception:
                    pass

                # EQ processing
                eq_result = {}
                try:
                    eq_result = eq.process(command) or {}
                except Exception as e:
                    log.debug(f"EQ error: {e}")
                    eq_result = {}

                # EQ early returns
                if eq_result.get("is_adult"):
                    resp = _to_str(eq_result.get("adult_response", "Let's keep this professional, Sir."))
                    self.speak(resp)
                    self.gui.set_status("Ready")
                    return
                if eq_result.get("is_gaali"):
                    resp = _to_str(eq_result.get("savage_response", "Watch it, Sir."))
                    self.speak(resp)
                    self.gui.set_status("Ready")
                    return
                if eq_result.get("is_love"):
                    resp = _to_str(eq_result.get("love_response", "Noted, Sir."))
                    self.speak(resp)
                    self.gui.set_status("Ready")
                    return

                # Route
                routed_list = []
                try:
                    routed_list = router.route(command) or []
                except Exception as e:
                    log.debug(f"Router error: {e}")
                    routed_list = []

                if not routed_list:
                    response = self._handle_general_chat(command, eq_result)
                    if response:
                        try:
                            context.add_jarvis(response)
                        except Exception:
                            pass
                        self.speak(response)
                    self.gui.set_status("Ready")
                    return

                # Process each intent
                for routed in routed_list:
                    if not isinstance(routed, dict):
                        continue

                    action = _to_str(routed.get("action", "general"))
                    target = _to_str(routed.get("target", ""))

                    log.info(f"Action={action}  target={target}")

                    response = ""
                    try:
                        response = self._dispatch(action, target, command, routed, eq_result)
                    except Exception as e:
                        log.error(f"Dispatch error: {e}")
                        response = "Something broke there, Sir. Try again?"

                    response = _to_str(response).strip()

                    if response:
                        try:
                            context.add_jarvis(response)
                        except Exception:
                            pass
                        self.speak(response)

                self.gui.set_status("Ready")

                # Learning
                try:
                    continuous_learner.observe(command)
                except Exception as e:
                    log.debug(f"Learner: {e}")

            except Exception as e:
                log.error(f"Process error: {e}")
                try:
                    self.speak("Something went wrong, Sir. Please try again.")
                    self.gui.set_status("Error")
                    QTimer.singleShot(2000, lambda: self.gui.set_status("Ready"))
                except Exception:
                    pass

    # =========================================================
    #  DISPATCH
    # =========================================================
    def _dispatch(self, action: str, target: str, command: str,
                  routed: dict, eq_result: dict) -> str:
        """Route action keyword to the correct handler."""

        action    = _to_str(action).lower()
        target    = _to_str(target)
        cmd_lower = _to_str(command).lower()

        # Mode switch
        if action == "mode_switch":
            return self._handle_mode_switch(target)

        # Exit
        if action == "exit":
            self._goodbye_said = True
            QTimer.singleShot(3000, self.shutdown)
            return "Goodbye, Sir. Always here when you need me."

        # App open
        if action == "open":
            try:
                self.gui.play_sound("task_complete")
            except Exception:
                pass
            try:
                result = app_registry.open(target)
                return _safe_dict_get(result, "message", "Done, Sir.")
            except Exception as e:
                log.error(f"Open error: {e}")
                return "Couldn't open that, Sir."

        if action == "close":
            try:
                result = app_registry.close(target)
                return _safe_dict_get(result, "message", "Done, Sir.")
            except Exception:
                return "Close failed, Sir."

        # System controls
        if action == "system":
            return self._handle_system(target, command)

        # Web AI (Claude, ChatGPT, Gemini via Selenium)
        if action == "web_ai":
            try:
                params    = routed.get("params", {}) if isinstance(routed.get("params"), dict) else {}
                preferred = _to_str(params.get("ai_service") or routed.get("ai_service", ""))
                preferred = preferred or None
                self.gui.set_status("Opening AI site...")

                def status_cb(msg):
                    try:
                        msg_s = _to_str(msg)
                        self.gui.set_status(msg_s[:40])
                        self.speak(msg_s)
                    except Exception:
                        pass

                query  = _to_str(params.get("query") or routed.get("query") or command)
                result = web_ai.ask(query=query, preferred=preferred, on_status=status_cb)
                return _safe_dict_get(result, "speech_text", "Done, Sir.")
            except Exception as e:
                log.error(f"Web AI: {e}")
                return "Web AI failed, Sir."

        # Scanning mode action
        if action == "scan":
            try:
                if mode_manager.current_mode != Mode.SCANNING:
                    return "Sir, please activate Scanning mode first."
                result = scanning_mode.run(command)
                return _safe_dict_get(result, "message", "Scan complete.")
            except Exception:
                return "Scan failed, Sir."

        # Phishing / URL check
        if action == "phishing_check" or "phishing" in cmd_lower or "check url" in cmd_lower:
            try:
                url = None
                if hasattr(phishing, "extract_url"):
                    url = phishing.extract_url(command)
                if not url:
                    m = re.search(
                        r'(?:https?://)?(?:[-\w]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?',
                        command,
                    )
                    if m:
                        url = m.group(0)

                if not url:
                    self._request_security_input("url")
                    return ""

                self.gui.set_status("Analyzing URL...")
                try:
                    is_online = net.is_online()
                except Exception:
                    is_online = False
                result = phishing.analyze(url, deep_check=is_online)

                try:
                    if isinstance(result, dict):
                        self.gui.show_security_result(result)
                except Exception:
                    pass

                if hasattr(phishing, "format_for_speech"):
                    return _to_str(phishing.format_for_speech(result))

                if isinstance(result, dict):
                    risk    = result.get("risk_score", 0)
                    verdict = _to_str(result.get("verdict", ""))
                    reasons = result.get("reasons", [])
                    msg     = f"Risk score {risk} out of 100, Sir. {verdict}"
                    if reasons and risk >= 40 and isinstance(reasons, list) and len(reasons) > 0:
                        msg += f" Issue: {_to_str(reasons[0])}."
                    return msg
                return "Scan done, Sir."
            except Exception as e:
                log.error(f"Phishing: {e}")
                return "URL check failed, Sir."

        # Password strength + breach check
        if (action == "check_password"
                or "check password" in cmd_lower
                or "password strength" in cmd_lower
                or "password check" in cmd_lower):
            try:
                self._request_security_input("password")
                return "Please type the password in the input panel, Sir."
            except Exception as e:
                log.error(f"Password check request: {e}")
                return "Couldn't open password check panel, Sir."

        # Email breach check
        if (action == "check_email"
                or "check email" in cmd_lower
                or "email breach" in cmd_lower):
            try:
                email_match = re.search(
                    r'[\w.\-+]+@[\w.\-]+\.[a-zA-Z]{2,}', command
                )
                if email_match:
                    email  = email_match.group(0)
                    result = security_mode.check_email_breach(email)
                    return _safe_dict_get(result, "message", "Check done, Sir.")
                self._request_security_input("email")
                return "Please type the email address in the input panel, Sir."
            except Exception as e:
                log.error(f"Email breach check: {e}")
                return "Email check failed, Sir."

        # Weather
        if action == "weather":
            try:
                city   = target or "Pune"
                result = weather.current(city)
                return _safe_dict_get(result, "message", "Weather unavailable.")
            except Exception:
                return "Weather unavailable, Sir."

        # News
        if action == "news":
            try:
                result = news.top_headlines(count=3)
                return _safe_dict_get(result, "message", "No news right now.")
            except Exception:
                return "News unavailable, Sir."

        # Math / Wolfram
        if action in ("math", "wolfram"):
            try:
                result = wolfram.ask(command)
                return _safe_dict_get(result, "message", "Couldn't solve that, Sir.")
            except Exception:
                return "Wolfram failed, Sir."

        # Search
        if action in ("search", "realtime"):
            try:
                self.gui.set_status("Searching...")
                try:
                    self.speak(_to_str(loading_phrases.get("searching")))
                except Exception:
                    try:
                        self.speak(_to_str(loading_phrases.get("search")))
                    except Exception:
                        pass

                params = routed.get("params", {}) if isinstance(routed.get("params"), dict) else {}
                query  = _to_str(params.get("query") or target or command)
                result = rts.ask(query)
                return _safe_dict_get(result, "message", "No results.")
            except Exception as e:
                log.error(f"Search: {e}")
                return "Search failed, Sir."

        # Image generation
        if action == "generate_image":
            try:
                prompt = target or command
                self.gui.set_status("Generating images...")
                result = image_gen.start(
                    prompt=prompt,
                    on_ready=lambda p: log.info(f"Image ready: {p}"),
                    on_speak=self.speak,
                )
                return _safe_dict_get(result, "message", "")
            except Exception:
                return "Image gen failed, Sir."

        if action == "next_image":
            try:
                result = image_gen.next()
                return _safe_dict_get(result, "message", "No more images.")
            except Exception:
                return "No image queue, Sir."

        if action == "stop_image":
            try:
                result = image_gen.stop()
                return _safe_dict_get(result, "message", "Stopped.")
            except Exception:
                return "Done, Sir."

        # Music / Spotify
        if action in ("music", "spotify", "play"):
            try:
                if "pause" in cmd_lower:
                    r = spotify.pause()
                elif "next" in cmd_lower or "skip" in cmd_lower:
                    r = spotify.next_track()
                elif "previous" in cmd_lower or "back" in cmd_lower:
                    r = spotify.previous_track()
                elif target:
                    r = spotify.search_and_play(target)
                else:
                    r = spotify.play()
                return _safe_dict_get(r, "message", "Done, Sir.")
            except Exception:
                return "Music control failed, Sir."

        # WhatsApp
        if action == "whatsapp":
            try:
                parsed = whatsapp.parse_command(command)
                if isinstance(parsed, tuple) and len(parsed) >= 2:
                    name, message = parsed[0], parsed[1]
                else:
                    name, message = None, None

                if name and message:
                    r = whatsapp.send(_to_str(name), _to_str(message))
                    return _safe_dict_get(r, "message", "Done, Sir.")
                return "Sir, try: 'send hi to Rahul'."
            except Exception:
                return "WhatsApp failed, Sir."

        # Personal data save
        if action == "save_data":
            try:
                personal_data_extractor.trigger(on_speak=self.speak)
                return ""
            except Exception:
                return "Data save failed, Sir."

        # Clear data
        if action == "clear_data":
            return "Sir, to clear memory, say 'yes clear all memory' to confirm."

        # Recall from memory
        if action == "recall":
            try:
                results = memory.recall(target or command)
                if results and isinstance(results, list) and len(results) > 0:
                    first = results[0]
                    if isinstance(first, dict):
                        return f"I remember: {_to_str(first.get('fact', ''))}"
                    return f"I remember: {_to_str(first)}"
                return "Nothing saved on that, Sir."
            except Exception:
                return "Recall failed, Sir."

        # Vault save (Companion mode only)
        if action == "vault_save":
            if mode_manager.current_mode != Mode.COMPANION:
                return "Vault access needs Companion mode, Sir."
            try:
                companion_mode.save_to_vault("memory", target or command)
                return "Saved to vault, Deep."
            except Exception:
                return "Vault save failed, Deep."

        # Vault recall (Companion mode only)
        if action == "vault_recall":
            if mode_manager.current_mode != Mode.COMPANION:
                return "Vault access needs Companion mode, Sir."
            try:
                entries = companion_mode.recall_from_vault(target or "", limit=3)
                if entries and isinstance(entries, list):
                    return f"Found {len(entries)} memories, Deep."
                return "Nothing matching that, Deep."
            except Exception:
                return "Vault recall failed, Deep."

        # Default: general chat
        return self._handle_general_chat(command, eq_result)

    # =========================================================
    #  GENERAL CHAT
    # =========================================================
    def _handle_general_chat(self, command: str, eq_result: dict) -> str:
        try:
            self.gui.set_status("Thinking...")
            response = chatbot.ask(
                query=_to_str(command),
                use_context=True,
                temperature=0.7,
            )
            response = _to_str(response).strip()
            return response if response else "I'm not sure, Sir."
        except Exception as e:
            log.error(f"Chat error: {e}")
            return "Sir, having trouble thinking right now."

    # =========================================================
    #  MODE SWITCH
    # =========================================================
    def _handle_mode_switch(self, target: str) -> str:
        target_lower = _to_str(target).lower().strip()

        mode_map = {
            "neural":    Mode.NEURAL,
            "default":   Mode.NEURAL,
            "normal":    Mode.NEURAL,
            "security":  Mode.SECURITY,
            "scanning":  Mode.SCANNING,
            "scan":      Mode.SCANNING,
            "companion": Mode.COMPANION,
            "gaming":    Mode.GAMING,
            "game":      Mode.GAMING,
        }

        new_mode = mode_map.get(target_lower)
        if not new_mode:
            return f"Unknown mode '{target}', Sir."

        self._exit_current_mode()

        # Companion requires password gate
        if new_mode == Mode.COMPANION:
            self._awaiting_password = True
            try:
                self.gui.show_password_screen()
            except Exception as e:
                log.error(f"Password screen: {e}")
                self._awaiting_password = False
                return "Couldn't show password screen, Sir."
            return ""

        try:
            mode_manager.switch(new_mode)
            self._enter_mode(new_mode)
        except Exception as e:
            log.error(f"Mode switch: {e}")
            return "Mode switch failed, Sir."
        return ""

    def _exit_current_mode(self):
        curr = mode_manager.current_mode
        handlers = {
            Mode.SECURITY:  security_mode,
            Mode.SCANNING:  scanning_mode,
            Mode.COMPANION: companion_mode,
            Mode.GAMING:    gaming_mode,
            Mode.OFFLINE:   offline_mode,
            Mode.NEURAL:    neural_mode,
        }
        h = handlers.get(curr)
        if h:
            try:
                h.exit(on_speak=None)
            except Exception as e:
                log.debug(f"Exit {curr}: {e}")

    def _enter_mode(self, mode: Mode):
        handlers = {
            Mode.NEURAL:    neural_mode,
            Mode.SECURITY:  security_mode,
            Mode.SCANNING:  scanning_mode,
            Mode.COMPANION: companion_mode,
            Mode.GAMING:    gaming_mode,
            Mode.OFFLINE:   offline_mode,
        }
        h = handlers.get(mode)
        if h:
            try:
                h.enter(on_speak=self.speak)
            except Exception as e:
                log.error(f"Enter {mode}: {e}")

    # =========================================================
    #  SYSTEM CONTROL
    # =========================================================
    def _handle_system(self, target: str, command: str) -> str:
        cmd = _to_str(command).lower()

        try:
            if "volume up" in cmd or "vol up" in cmd:
                r = system.volume_up()
            elif "volume down" in cmd or "vol down" in cmd:
                r = system.volume_down()
            elif "mute" in cmd and "un" not in cmd:
                r = system.mute()
            elif "unmute" in cmd:
                r = system.unmute()
            elif "brightness up" in cmd:
                r = system.brightness_up()
            elif "brightness down" in cmd:
                r = system.brightness_down()
            elif "screenshot" in cmd:
                r = system.screenshot()
            elif "start recording" in cmd or "record screen" in cmd:
                r = system.start_recording()
            elif "stop recording" in cmd:
                r = system.stop_recording()
            elif "lock" in cmd and "screen" in cmd:
                confirmed = "yes" in cmd or "confirm" in cmd
                r = system.lock_screen(confirmed=confirmed)
            elif "bluetooth on" in cmd:
                r = system.bluetooth_on()
            elif "bluetooth off" in cmd:
                r = system.bluetooth_off()
            elif "battery" in cmd:
                bat = system.battery_status()
                if isinstance(bat, dict):
                    plugged = "Plugged in." if bat.get("plugged") else "On battery."
                    return f"Battery at {bat.get('percent', '?')}%, Sir. {plugged}"
                return "Battery info unavailable, Sir."
            else:
                return "Sir, what system setting?"

            return _safe_dict_get(r, "message", "Done, Sir.")
        except Exception as e:
            log.error(f"System control: {e}")
            return "System control failed, Sir."

    # =========================================================
    #  PASSWORD FLOW
    # =========================================================
    def _on_password_submitted(self, pw: str):
        pw = _to_str(pw).strip()
        try:
            result = companion_mode.verify_password(pw)
        except Exception as e:
            log.error(f"Password verify: {e}")
            self.gui.password_error("Verification failed, try again.")
            return

        if isinstance(result, dict) and result.get("ok"):
            self._awaiting_password = False
            try:
                self.gui.password_success()
            except Exception:
                pass
            try:
                mode_manager.switch(Mode.COMPANION)
                QTimer.singleShot(1000, lambda: companion_mode.enter(on_speak=self.speak))
            except Exception as e:
                log.error(f"Companion enter: {e}")
        else:
            err = _safe_dict_get(result, "message", "Wrong code.")
            try:
                self.gui.password_error(err)
            except Exception:
                pass
            if isinstance(result, dict) and result.get("locked_out"):
                self._awaiting_password = False
                QTimer.singleShot(2000, lambda: self.gui.hide_password_screen())

    def _on_password_cancelled(self):
        self._awaiting_password = False
        self.speak("Okay Sir, staying in Neural mode.")

    # =========================================================
    #  SECURITY INPUT FLOW
    # =========================================================
    def _request_security_input(self, mode: str):
        """
        Open the SecurityInputDialog for the given check type.
        Pauses the voice listen loop while the overlay is shown.
        mode: "url" | "password" | "email"
        """
        self._awaiting_security_input = True
        self._security_input_mode     = mode
        try:
            self.gui.show_security_input(mode)
            prompt_map = {
                "url":      "Please type or paste the URL in the input panel, Sir.",
                "password": "Please type the password in the input panel, Sir. It stays local.",
                "email":    "Please type the email address in the input panel, Sir.",
            }
            prompt = prompt_map.get(mode, "Please type the value in the input panel, Sir.")
            self.speak(prompt)
        except Exception as e:
            log.error(f"Show security input: {e}")
            self._awaiting_security_input = False

    def _on_security_input(self, value: str, mode: str):
        """
        Fired when user submits a value through the SecurityInputDialog.
        Runs the appropriate security check and pushes the result back
        to the dialog's inline result area.
        """
        value = _to_str(value).strip()
        mode  = _to_str(mode).strip()
        log.info(f"Security input received: mode={mode}  value_len={len(value)}")

        if not value:
            try:
                self.gui.security_input_dialog.show_error("Nothing entered, Sir.")
            except Exception:
                pass
            return

        try:
            if mode == "url":
                self.gui.set_status("Analyzing URL...")
                try:
                    is_online = net.is_online()
                except Exception:
                    is_online = False

                try:
                    result = phishing.analyze(value, deep_check=is_online)
                except Exception:
                    result = security_mode.check_url(value)

                try:
                    if isinstance(result, dict):
                        self.gui.show_security_result(result)
                except Exception:
                    pass

                if hasattr(phishing, "format_for_speech") and isinstance(result, dict):
                    speech = _to_str(phishing.format_for_speech(result))
                elif isinstance(result, dict):
                    risk    = result.get("risk_score", 0)
                    verdict = _to_str(result.get("verdict", ""))
                    speech  = f"Risk score {risk}. {verdict}"
                else:
                    speech = "URL analysis done, Sir."
                self.speak(speech)

            elif mode == "password":
                self.gui.set_status("Analysing password...")

                strength_result = security_mode.password_strength(value)

                breach_result = {}
                try:
                    if net.is_online():
                        breach_result = security_mode.check_password(value)
                except Exception:
                    pass

                merged = dict(strength_result)
                score  = strength_result.get("score", 0)
                s_str  = strength_result.get("strength", "?").upper()
                issues = strength_result.get("issues", [])

                if breach_result.get("ok") and breach_result.get("breached"):
                    count = breach_result.get("count", 0)
                    merged["verdict"] = (
                        f"{s_str} strength — also seen in {count:,} data breaches! "
                        f"Change it immediately, Sir."
                    )
                elif breach_result.get("ok") and not breach_result.get("breached"):
                    merged["verdict"] = (
                        f"{s_str} strength — not found in known breaches, Sir."
                    )
                else:
                    merged["verdict"] = (
                        f"{s_str} strength. "
                        + (f"Issues: {', '.join(issues[:2])}." if issues else "")
                    )

                try:
                    self.gui.show_security_result(merged)
                except Exception:
                    pass

                self.speak(_to_str(merged.get("verdict", "Analysis done, Sir.")))

            elif mode == "email":
                self.gui.set_status("Checking email breach...")
                result = security_mode.check_email_breach(value)
                try:
                    ok      = result.get("ok", False)
                    display = {
                        "safe":       not ok,
                        "risk_score": 80 if ok else 0,
                        "verdict":    _to_str(result.get("message", "")),
                        "reasons":    [],
                    }
                    self.gui.show_security_result(display)
                except Exception:
                    pass
                self.speak(_to_str(result.get("message", "Email check done, Sir.")))

            else:
                log.warn(f"Unknown security input mode: {mode}")

            self.gui.set_status("Ready")

        except Exception as e:
            log.error(f"Security input processing: {e}")
            self.speak("Something went wrong with that check, Sir.")

    def _on_security_input_cancelled(self):
        """User pressed Cancel on the SecurityInputDialog."""
        self._awaiting_security_input = False
        self._security_input_mode     = "url"
        log.info("Security input cancelled — returning to main HUD.")

    # =========================================================
    #  SHUTDOWN
    # =========================================================
    def shutdown(self):
        log.info("Shutdown initiated.")
        self.running = False

        if self._goodbye_said:
            time.sleep(3)

        daemons = [
            ("proactive_checkin", lambda: proactive_checkin.stop()),
            ("notif_listener",    lambda: notif_listener.stop()),
            ("image_gen",         lambda: image_gen.stop()),
            ("tts",               lambda: tts.stop_all() if hasattr(tts, "stop_all") else None),
            ("stt",               lambda: stt.shutdown() if hasattr(stt, "shutdown") else None),
            ("task_mgr",          lambda: task_mgr.stop_all() if hasattr(task_mgr, "stop_all") else None),
        ]
        for name, fn in daemons:
            try:
                fn()
            except Exception as e:
                log.debug(f"Shutdown {name}: {e}")

        try:
            self._exit_current_mode()
        except Exception:
            pass

        log.info("Shutdown complete.")

        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.quit()
        except Exception:
            pass


# =============================================================
#  ENTRY POINT
# =============================================================
def main():
    def signal_handler(sig, frame):
        log.info("Ctrl+C — shutting down...")
        if jarvis:
            jarvis.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    app = get_app()
    gui = get_gui()
    gui.show()

    global jarvis
    jarvis       = JarvisCore()
    jarvis.gui   = gui
    jarvis.startup()

    try:
        exit_code = app.exec_()
    except Exception as e:
        log.error(f"Event loop: {e}")
        exit_code = 1
    finally:
        if jarvis:
            try:
                jarvis.shutdown()
            except Exception:
                pass

    sys.exit(exit_code)


jarvis: Optional[JarvisCore] = None


if __name__ == "__main__":
    main()