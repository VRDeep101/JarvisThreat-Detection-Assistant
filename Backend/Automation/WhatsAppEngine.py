# =============================================================
#  Backend/Automation/WhatsAppEngine.py - WhatsApp Messaging
#
#  CHANGES vs original:
#    - send(): better timeout + specific error messages
#    - scan_unread(): Chrome/Selenium availability guard at top
#    - All other logic identical to original
#
#  Usage:
#    from Backend.Automation.WhatsAppEngine import whatsapp
#    whatsapp.add_contact("Rahul", "9876543210")
#    whatsapp.send("Rahul", "meeting at 5")
#    count = whatsapp.scan_unread()
# =============================================================

import json
import re
import time
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

from Backend.Utils.Logger import get_logger
from Backend.Utils.PathResolver import paths
from Backend.Automation.AppRegistry import app_registry

log = get_logger("WhatsApp")

# -- Deps -----------------------------------------------------
try:
    import pywhatkit
    PYWHATKIT_OK = True
except ImportError:
    PYWHATKIT_OK = False
    log.warn("pywhatkit not installed — WhatsApp send unavailable")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    PYAUTOGUI_OK = True
except Exception:
    PYAUTOGUI_OK = False

try:
    import psutil
    PSUTIL_OK = True
except Exception:
    PSUTIL_OK = False

# Selenium availability check (used by scan_unread only)
try:
    from selenium import webdriver as _selenium_webdriver_check
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False

# =============================================================
#  Contact Storage helpers
# =============================================================
CONTACTS_PATH = paths.WHATSAPP_CONTACTS


def _load_contacts() -> Dict[str, str]:
    try:
        if CONTACTS_PATH.exists():
            with open(CONTACTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.error(f"Contacts load: {e}")
    return {}


def _save_contacts(contacts: Dict[str, str]) -> None:
    try:
        with open(CONTACTS_PATH, "w", encoding="utf-8") as f:
            json.dump(contacts, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error(f"Contacts save: {e}")


# =============================================================
#  Phone number normalization
# =============================================================
def _normalize_phone(phone: str) -> str:
    """Normalize to +91XXXXXXXXXX format for India numbers."""
    phone = phone.strip().replace(" ", "").replace("-", "")

    if phone.startswith("0"):
        phone = "+91" + phone[1:]
    elif phone.isdigit() and len(phone) == 10:
        phone = "+91" + phone
    elif not phone.startswith("+"):
        phone = "+" + phone
    return phone


# =============================================================
#  WhatsAppEngine
# =============================================================
class WhatsAppEngine:
    """WhatsApp message sending + contact management."""

    # =========================================================
    #  CONTACTS
    # =========================================================
    def add_contact(self, name: str, phone: str) -> Dict:
        if not name or not phone:
            return {"ok": False, "message": "Need name and number, Sir."}

        name_key  = name.strip().lower()
        phone_norm = _normalize_phone(phone)

        digits = re.sub(r"[^\d]", "", phone_norm)
        if len(digits) < 10:
            return {"ok": False, "message": f"'{phone}' doesn't look like a valid number, Sir."}

        contacts = _load_contacts()
        contacts[name_key] = phone_norm
        _save_contacts(contacts)

        log.action(f"Contact saved: {name_key} -> {phone_norm}")
        return {
            "ok": True,
            "message": f"Contact saved, Sir. {name.title()}: {phone_norm}",
        }

    def remove_contact(self, name: str) -> Dict:
        name_key = name.strip().lower()
        contacts = _load_contacts()
        if name_key in contacts:
            del contacts[name_key]
            _save_contacts(contacts)
            return {"ok": True, "message": f"Removed {name.title()} from contacts, Sir."}
        return {"ok": False, "message": f"{name.title()} isn't in contacts, Sir."}

    def list_contacts(self) -> Dict:
        contacts = _load_contacts()
        if not contacts:
            return {
                "ok": True,
                "message": "No contacts saved, Sir. Say 'add contact [name] [number]' to save one.",
                "contacts": {},
            }
        lines = [f"  {n.title():15} : {p}" for n, p in contacts.items()]
        return {
            "ok": True,
            "message": "Saved WhatsApp contacts:\n" + "\n".join(lines),
            "contacts": contacts,
        }

    def get_phone(self, name: str) -> Optional[str]:
        contacts = _load_contacts()
        return contacts.get(name.strip().lower())

    # =========================================================
    #  SEND MESSAGE  ← FIXED: better timeout + error messages
    # =========================================================
    def send(self, name: str, message: str) -> Dict:
        """
        Send WhatsApp message to contact via pywhatkit (WhatsApp Web).
        Returns dict with ok, message keys.
        """
        if not PYWHATKIT_OK:
            return {
                "ok": False,
                "message": "pywhatkit not installed, Sir. Run: pip install pywhatkit",
            }

        phone = self.get_phone(name)
        if not phone:
            return {
                "ok": False,
                "message": (
                    f"Contact '{name.title()}' not found, Sir. "
                    f"Say 'add contact {name} [number]' to save first."
                ),
                "needs_contact": True,
            }

        log.action(f"Sending to {name} ({phone}): {message[:40]}")

        try:
            pywhatkit.sendwhatmsg_instantly(
                phone_no=phone,
                message=message,
                wait_time=18,     # seconds to wait for WhatsApp Web to load
                tab_close=True,   # close browser tab after sending
                close_time=5,     # seconds before closing the tab
            )

            # Give pywhatkit a moment to finish closing the tab
            time.sleep(5)

            log.action(f"WhatsApp send OK → {name} ({phone})")
            return {
                "ok": True,
                "message": f"Message sent to {name.title()}, Sir.",
            }

        except TimeoutError:
            log.error(f"WhatsApp send timeout → {name}")
            return {
                "ok": False,
                "message": (
                    f"Timeout sending to {name.title()}, Sir. "
                    "WhatsApp Web is taking too long. Try again."
                ),
            }

        except Exception as e:
            log.error(f"WhatsApp send error → {name}: {e}")
            error_str = str(e).lower()

            # Give a specific message based on the error type
            if "chrome" in error_str or "chromedriver" in error_str:
                msg = (
                    "Chrome browser error, Sir. "
                    "Make sure Chrome is installed at the default location."
                )
            elif "selenium" in error_str:
                msg = (
                    "Browser automation error, Sir. "
                    "Run: pip install selenium webdriver-manager"
                )
            elif "pyautogui" in error_str or "failsafe" in error_str:
                msg = "Automation error, Sir. Try again in a moment."
            elif "connection" in error_str or "network" in error_str:
                msg = "Network error, Sir. Check your internet connection."
            else:
                msg = (
                    f"Couldn't send to {name.title()}, Sir. "
                    "Make sure WhatsApp Web is logged in."
                )

            return {"ok": False, "message": msg}

    # =========================================================
    #  PARSE COMMAND
    # =========================================================
    def parse_command(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse natural language WhatsApp commands.
        Examples:
          'send hi to rahul'
          'whatsapp maa that I'll be late'
          'message vishakha say happy birthday'
        Returns (name, message) or (None, None).
        """
        q = query.lower()

        patterns = [
            r'send\s+(.+?)\s+to\s+([a-z]+)',
            r'(?:message|whatsapp|text)\s+([a-z]+)\s+(?:saying|that|:|-)\s*(.+)',
            r'(?:send\s+message|message)\s+to\s+([a-z]+)\s*(?::|,)?\s*(.+)',
            r'whatsapp\s+([a-z]+)\s+(.+)',
        ]

        for pattern in patterns:
            m = re.search(pattern, q)
            if m:
                groups = m.groups()
                if pattern.startswith(r'send\s+(.+?)\s+to\s+'):
                    message, name = groups   # "send X to Y" → msg first
                else:
                    name, message = groups

                name    = name.strip()
                message = message.strip(" .,:-")
                message = re.sub(r'\s+on\s+whatsapp\s*$', '', message)

                if name and message:
                    return name, message

        return None, None

    # =========================================================
    #  UNREAD COUNT SCAN  ← FIXED: guard before trying Selenium
    # =========================================================
    def scan_unread(self, silent: bool = True, timeout: int = 20) -> Dict:
        """
        Open WhatsApp Web briefly, count unread badges, close.
        silent=True → minimised window (runs in background).
        Returns {"ok": bool, "count": int, "senders": list, "message": str}
        """
        # Guard: check dependencies before attempting anything
        if not SELENIUM_OK:
            return {
                "ok": False,
                "count": 0,
                "message": "Selenium not installed, Sir. Run: pip install selenium webdriver-manager",
            }

        # Import Selenium components (already confirmed available above)
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
        except ImportError as ie:
            return {
                "ok": False,
                "count": 0,
                "message": f"Selenium import error, Sir: {ie}",
            }

        opts = Options()
        opts.add_argument(f"--user-data-dir={paths.CHROME_USER_DATA}")
        opts.add_argument("--profile-directory=JarvisAI")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")

        if silent:
            opts.add_argument("--window-position=-32000,-32000")
            opts.add_argument("--window-size=800,600")

        driver = None
        try:
            # Try without explicit service first (Chrome on PATH)
            try:
                driver = webdriver.Chrome(options=opts)
            except Exception:
                driver = webdriver.Chrome(
                    service=Service(ChromeDriverManager().install()),
                    options=opts,
                )

            driver.get("https://web.whatsapp.com/")

            # Wait for chat list or QR code
            t0 = time.time()
            chat_list_visible = False
            while time.time() - t0 < timeout:
                try:
                    chats = driver.find_elements(By.CSS_SELECTOR, "[aria-label='Chat list']")
                    if chats:
                        chat_list_visible = True
                        break
                    qr = driver.find_elements(By.CSS_SELECTOR, "[data-ref]")
                    if qr:
                        break
                except Exception:
                    pass
                time.sleep(1)

            if not chat_list_visible:
                return {
                    "ok": False,
                    "count": 0,
                    "message": "WhatsApp Web not logged in, Sir. Scan QR in the JarvisAI Chrome profile.",
                }

            time.sleep(2)

            # Count unread badges
            unread_elements = driver.find_elements(
                By.CSS_SELECTOR, "span[aria-label*='unread']"
            )

            count   = 0
            senders = []

            for el in unread_elements:
                try:
                    label = el.get_attribute("aria-label") or ""
                    m = re.search(r'(\d+)\s+unread', label)
                    if m:
                        count += int(m.group(1))
                except Exception:
                    continue

            # Try to find sender names
            try:
                unread_chats = driver.find_elements(By.CSS_SELECTOR, "div[role='row']")
                for chat in unread_chats[:10]:
                    try:
                        badge = chat.find_elements(By.CSS_SELECTOR, "span[aria-label*='unread']")
                        if badge:
                            name_els = chat.find_elements(By.CSS_SELECTOR, "span[title]")
                            if name_els:
                                name = name_els[0].get_attribute("title")
                                if name:
                                    senders.append(name)
                    except Exception:
                        continue
            except Exception:
                pass

            log.info(f"WhatsApp unread scan: {count} messages from {len(senders)} senders")

            return {
                "ok": True,
                "count": count,
                "senders": senders[:5],
                "message": f"{count} unread messages" if count else "All caught up, Sir.",
            }

        except Exception as e:
            log.error(f"WhatsApp scan error: {e}")
            err_str = str(e).lower()
            if "chrome" in err_str or "chromedriver" in err_str:
                msg = "Chrome not found, Sir. Install Chrome from google.com/chrome"
            elif "session" in err_str or "webdriver" in err_str:
                msg = "WebDriver error, Sir. Run: pip install --upgrade selenium webdriver-manager"
            else:
                msg = f"WhatsApp scan failed, Sir: {str(e)[:80]}"
            return {"ok": False, "count": 0, "message": msg}

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass


# =============================================================
#  Singleton
# =============================================================
whatsapp = WhatsAppEngine()


# =============================================================
#  Test block — python -m Backend.Automation.WhatsAppEngine
# =============================================================
if __name__ == "__main__":
    print("\n--- WhatsAppEngine Test ---\n")

    print("-- Dependency check --")
    print(f"  pywhatkit  : {'OK' if PYWHATKIT_OK else 'MISSING — pip install pywhatkit'}")
    print(f"  pyautogui  : {'OK' if PYAUTOGUI_OK else 'MISSING — pip install pyautogui'}")
    print(f"  psutil     : {'OK' if PSUTIL_OK else 'MISSING — pip install psutil'}")
    print(f"  selenium   : {'OK' if SELENIUM_OK else 'MISSING — pip install selenium'}")

    print("\n-- Command parsing --")
    tests = [
        "send hi to rahul",
        "whatsapp maa that I'll be late",
        "message vishakha saying happy birthday",
        "send message to naveen: meeting at 5",
    ]
    for t in tests:
        name, msg = whatsapp.parse_command(t)
        print(f"  '{t[:45]:<45}' -> name='{name}' msg='{msg}'")

    print("\n-- Contact management --")
    r = whatsapp.add_contact("test_contact_99", "9876543210")
    print(f"  Add: {r}")

    phone = whatsapp.get_phone("test_contact_99")
    print(f"  Get: {phone}")

    r = whatsapp.list_contacts()
    print(f"  List:\n{r['message']}")

    r = whatsapp.remove_contact("test_contact_99")
    print(f"  Remove: {r}")

    print("\n[OK] WhatsAppEngine test complete\n")