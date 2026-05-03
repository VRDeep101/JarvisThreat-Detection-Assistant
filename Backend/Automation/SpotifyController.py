# =============================================================
#  Backend/Automation/SpotifyController.py - Spotify Hybrid
#
#  CHANGES vs original:
#    - _ensure_running(): wait_sec 5 → 15, UI-ready sleep 2 → 3
#    - _ensure_running(): wrapped launch in try/except with logging
#    - All other logic identical to original
#
#  Usage:
#    from Backend.Automation.SpotifyController import spotify
#    spotify.play()
#    spotify.pause()
#    spotify.next_track()
#    spotify.search_and_play("shape of you")
# =============================================================

import os
import subprocess
import time
import webbrowser
from typing import Dict, Optional

from Backend.Utils.Logger import get_logger
from Backend.Utils.PathResolver import paths
from Backend.Automation.AppRegistry import app_registry

log = get_logger("Spotify")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    PYAUTOGUI_OK = True
except Exception:
    PYAUTOGUI_OK = False

try:
    import keyboard as _kb
    KEYBOARD_OK = True
except Exception:
    KEYBOARD_OK = False

try:
    import psutil
    PSUTIL_OK = True
except Exception:
    PSUTIL_OK = False

try:
    import pygetwindow as gw
    PYGETWINDOW_OK = True
except Exception:
    PYGETWINDOW_OK = False


# =============================================================
#  SpotifyController
# =============================================================
class SpotifyController:
    """Spotify desktop + web hybrid controller."""

    WEB_PLAYER  = "https://open.spotify.com"
    SEARCH_URL  = "https://open.spotify.com/search/{}"

    # ── Running check ──────────────────────────────────────────
    def is_running(self) -> bool:
        """Return True if Spotify desktop process is running."""
        if not PSUTIL_OK:
            return False
        try:
            for p in psutil.process_iter(["name"]):
                if p.info["name"] and "spotify" in p.info["name"].lower():
                    return True
        except Exception:
            pass
        return False

    # ── Launch & wait ──────────────────────────────────────────
    def _ensure_running(self, wait_sec: int = 15) -> bool:
        """
        Launch Spotify if not running, then wait until the process
        is visible and the UI has had time to become interactive.

        wait_sec raised from 5 → 15 so slower machines don't time out.
        UI-ready sleep raised from 2 → 3 for the same reason.
        """
        if self.is_running():
            return True

        # Attempt launch via AppRegistry
        try:
            result = app_registry.open("spotify", prefer_desktop=True)
            if not result.get("ok"):
                log.warn("Spotify launch rejected by AppRegistry")
                return False
        except Exception as e:
            log.error(f"Spotify launch exception: {e}")
            return False

        # Poll until process appears or timeout
        t0 = time.time()
        while time.time() - t0 < wait_sec:
            if self.is_running():
                # Extra sleep for the UI to finish painting
                time.sleep(3)
                log.info("Spotify launched and ready.")
                return True
            time.sleep(0.5)

        log.error(f"Spotify still not running after {wait_sec}s — giving up")
        return False

    def _focus_spotify(self) -> bool:
        """Bring Spotify window to the foreground."""
        if not PYGETWINDOW_OK:
            return False
        try:
            wins = gw.getWindowsWithTitle("Spotify")
            for w in wins:
                if "spotify" in w.title.lower():
                    if w.isMinimized:
                        w.restore()
                    w.activate()
                    time.sleep(0.3)
                    return True
        except Exception as e:
            log.debug(f"Focus error: {e}")
        return False

    # =========================================================
    #  Playback controls (media keys — work globally)
    # =========================================================
    def play_pause(self) -> Dict:
        """Toggle play/pause via media key."""
        if not KEYBOARD_OK:
            return {"ok": False, "message": "keyboard library unavailable, Sir. Run: pip install keyboard"}
        try:
            _kb.press_and_release("play/pause media")
            log.action("Spotify play/pause")
            return {"ok": True, "message": "Done, Sir."}
        except Exception as e:
            log.error(f"play_pause error: {e}")
            return {"ok": False, "message": f"Playback key failed, Sir: {e}"}

    def play(self) -> Dict:
        """Start or resume playback, launching Spotify if needed."""
        if not self._ensure_running():
            return {
                "ok": False,
                "message": "Couldn't start Spotify, Sir. Is it installed?",
            }
        return self.play_pause()

    def pause(self) -> Dict:
        """Pause playback."""
        return self.play_pause()

    def next_track(self) -> Dict:
        """Skip to the next track via media key."""
        if not KEYBOARD_OK:
            return {"ok": False, "message": "keyboard library unavailable, Sir."}
        try:
            _kb.press_and_release("next track")
            log.action("Spotify next")
            return {"ok": True, "message": "Skipping, Sir."}
        except Exception as e:
            log.error(f"next_track error: {e}")
            return {"ok": False, "message": f"Skip failed, Sir: {e}"}

    def previous_track(self) -> Dict:
        """Go back to the previous track via media key."""
        if not KEYBOARD_OK:
            return {"ok": False, "message": "keyboard library unavailable, Sir."}
        try:
            _kb.press_and_release("previous track")
            log.action("Spotify previous")
            return {"ok": True, "message": "Going back, Sir."}
        except Exception as e:
            log.error(f"previous_track error: {e}")
            return {"ok": False, "message": f"Go-back failed, Sir: {e}"}

    # =========================================================
    #  Search & play a specific song
    # =========================================================
    def search_and_play(self, query: str) -> Dict:
        """
        Search Spotify for a song and start playback.
        Tries desktop search first (Ctrl+L), falls back to web player.
        """
        if not query or not query.strip():
            return {"ok": False, "message": "Need a song name, Sir."}

        # Method 1: Desktop search via keyboard
        if self._ensure_running() and PYAUTOGUI_OK:
            result = self._desktop_search(query)
            if result["ok"]:
                return result

        # Method 2: Web player fallback
        return self._web_search(query)

    def _desktop_search(self, query: str) -> Dict:
        """Use Spotify desktop Ctrl+L search bar."""
        if not self._focus_spotify():
            return {"ok": False, "message": "Couldn't focus Spotify window"}

        try:
            time.sleep(0.5)
            pyautogui.hotkey("ctrl", "l")    # open search
            time.sleep(0.8)

            pyautogui.typewrite(query, interval=0.02)
            time.sleep(0.8)

            pyautogui.press("enter")          # go to search results
            time.sleep(2.5)

            pyautogui.press("enter")          # attempt to play top result
            time.sleep(0.5)

            log.action(f"Spotify desktop search: {query}")
            return {"ok": True, "message": f"Playing {query} on Spotify, Sir."}

        except Exception as e:
            log.error(f"Desktop search error: {e}")
            return {"ok": False, "message": str(e)}

    def _web_search(self, query: str) -> Dict:
        """Open song search in Spotify web player as fallback."""
        try:
            from urllib.parse import quote
            url = self.SEARCH_URL.format(quote(query))
            webbrowser.open(url)
            log.action(f"Spotify web search: {query}")
            return {
                "ok": True,
                "message": f"Opening Spotify web search for '{query}', Sir. Click the song to play.",
            }
        except Exception as e:
            log.error(f"Web search fallback error: {e}")
            return {"ok": False, "message": f"Search failed, Sir: {e}"}

    # =========================================================
    #  Volume control (Spotify-specific via pycaw)
    # =========================================================
    def set_spotify_volume(self, percent: int) -> Dict:
        """
        Set Spotify application volume independently of system volume.
        Requires pycaw (pip install pycaw).
        percent: 0–100
        """
        percent = max(0, min(100, percent))   # clamp to valid range
        try:
            from pycaw.pycaw import AudioUtilities
            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                if session.Process and "spotify" in session.Process.name().lower():
                    interface = session.SimpleAudioVolume
                    interface.SetMasterVolume(percent / 100.0, None)
                    log.action(f"Spotify volume → {percent}%")
                    return {"ok": True, "message": f"Spotify volume at {percent}%, Sir."}
            return {"ok": False, "message": "Spotify not active, Sir. Start Spotify first."}
        except ImportError:
            return {"ok": False, "message": "pycaw not installed, Sir. Run: pip install pycaw"}
        except Exception as e:
            log.error(f"Volume error: {e}")
            return {"ok": False, "message": f"Volume control failed, Sir: {e}"}


# =============================================================
#  Singleton
# =============================================================
spotify = SpotifyController()


# =============================================================
#  Test block — python -m Backend.Automation.SpotifyController
# =============================================================
if __name__ == "__main__":
    print("\n--- SpotifyController Test ---\n")

    print("-- Dependency check --")
    print(f"  psutil       : {'OK' if PSUTIL_OK else 'MISSING — pip install psutil'}")
    print(f"  pyautogui    : {'OK' if PYAUTOGUI_OK else 'MISSING — pip install pyautogui'}")
    print(f"  keyboard     : {'OK' if KEYBOARD_OK else 'MISSING — pip install keyboard'}")
    print(f"  pygetwindow  : {'OK' if PYGETWINDOW_OK else 'MISSING — pip install pygetwindow'}")
    print(f"\n  Spotify running: {spotify.is_running()}")

    # Uncomment below for live tests:
    # print("\n-- Starting Spotify --")
    # started = spotify._ensure_running()
    # print(f"  Started: {started}")
    #
    # if started:
    #     time.sleep(3)
    #     print("\n-- Play/Pause --")
    #     print(spotify.play_pause())
    #     time.sleep(3)
    #
    #     print("\n-- Search and play --")
    #     print(spotify.search_and_play("shape of you"))

    print("\n[OK] SpotifyController test complete\n")