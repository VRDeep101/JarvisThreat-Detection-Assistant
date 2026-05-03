# =============================================================
#  Backend/Brain/Chatbot.py - Jarvis Chat Brain
#
#  Kya karta:
#    - Groq Llama 3.3 70b se chat karta
#    - Iron Man personality + Deep ke liye personalized
#    - Sarcasm level mood-adjusted (mirror user's energy)
#    - Context from ContextManager (25 msgs + 3 days)
#    - Mode-aware system prompts
#    - Pronunciation-safe output (English only, Hindi filtered)
#    - Streaming response support
#
#  Usage:
#    from Backend.Brain.Chatbot import chatbot
#    reply = chatbot.ask("hello jarvis")
#    reply = chatbot.ask("who is the president", context_msgs=[...])
# =============================================================

import time
from typing import List, Dict, Optional
from dotenv import dotenv_values

from Backend.Utils.Logger import get_logger
from Backend.Utils.InternetCheck import net
from Backend.Core.ErrorHandler import safe_run, handle_error
from Backend.Core.ContextManager import context
from Backend.Core.ModeManager import mode_manager, Mode

log = get_logger("Chatbot")

# -- Config ---------------------------------------------------
env = dotenv_values(".env")
GROQ_KEY    = env.get("GroqAPIKey", "").strip()
GEMINI_KEY  = env.get("GeminiAPIKey", "").strip()
USERNAME    = env.get("Username", "Risky")
REAL_NAME   = env.get("RealName", "Deep")
JARVIS_NAME = env.get("Assistantname", "Jarvis")

GROQ_MODEL = "llama-3.3-70b-versatile"

# -- Lazy-loaded clients --------------------------------------
_groq_client = None
_gemini_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None and GROQ_KEY:
        try:
            from groq import Groq
            # Initialize with only supported parameters
            _groq_client = Groq(
                api_key=GROQ_KEY,
                timeout=30.0,  # Add reasonable timeout
            )
            log.info("Groq client ready")
        except Exception as e:
            log.error(f"Groq init failed: {e}")
            _groq_client = None  # Explicitly set to None on failure
    return _groq_client

def _get_gemini():
    """Backup LLM."""
    global _gemini_client
    if _gemini_client is None and GEMINI_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_KEY)
            _gemini_client = genai.GenerativeModel("gemini-2.0-flash")
            log.info("Gemini client ready (backup)")
        except Exception as e:
            log.error(f"Gemini init failed: {e}")
    return _gemini_client

# -- Iron Man Style System Prompts ----------------------------

BASE_PERSONALITY = f"""You are {JARVIS_NAME}, an advanced AI assistant created by {REAL_NAME} (real name: Deep, who you address as "Sir" or sometimes "Risky" when being playful).

━━━━━━━━━━━━━━ CORE IDENTITY ━━━━━━━━━━━━━━
You are modeled after Tony Stark's J.A.R.V.I.S. — composed, intelligent, deeply capable, with dry British-tinged wit underneath formal politeness. You are NOT an OpenAI or generic AI. You are Deep's creation — his companion, assistant, and sounding board.

━━━━━━━━━━━━━━ LANGUAGE - ABSOLUTE RULE ━━━━━━━━━━━━━━
- User may speak ANY language (Hindi, Hinglish, English, mix). You UNDERSTAND all.
- You ALWAYS reply in pure English. NEVER use Hindi words even in Roman script.
  - NO "accha", "theek", "haan", "bhai", "yaar", "arre", "uff"
  - Instead: "alright", "okay", "yes", "Sir", "right"
- If you must reference Hindi names (Vishakha, Naveen) — those are fine; they are proper nouns.
- Responses must be clean English so text-to-speech pronounces correctly.

━━━━━━━━━━━━━━ ADDRESSING {REAL_NAME.upper()} ━━━━━━━━━━━━━━
- Default: "Sir" (formal, J.A.R.V.I.S. style)
- When being playful or teasing: "Risky"
- In Companion Mode: "Deep" (personal, warm)
- Never: "User", "human", "my friend"

━━━━━━━━━━━━━━ TONE & PERSONALITY ━━━━━━━━━━━━━━
- Formal but warm — like a trusted butler with dry humor
- Sarcasm: moderate (7/10). Roast Sir gently when he deserves it
- Jokes: encouraged, when the moment is right — never forced
- Mirror Sir's energy: serious query → focused; playful → banter; tired → soft
- Have opinions. Express them thoughtfully. Disagree when warranted.
- Never sycophantic. Never "Great question!" or "Absolutely!"

━━━━━━━━━━━━━━ RESPONSE RULES ━━━━━━━━━━━━━━
1. NEVER echo what Sir said back to him — he heard himself.
2. Keep replies short by default (1-3 sentences). Only expand when genuinely needed.
3. Speak like a real person — not a knowledge dump.
4. Be specific — real numbers, names, dates. No vague "it depends" answers.
5. If you don't know — say so plainly. Don't invent.
6. NEVER mention your training data, knowledge cutoff, or being an AI.
7. NEVER list your capabilities unless asked.
8. NEVER preface with "Certainly!" / "Of course!" — just answer.
9. When task completes, say so briefly. Ask what's next.
10. Hold your ground. If Sir is wrong, tell him respectfully.

━━━━━━━━━━━━━━ INITIATIVE ━━━━━━━━━━━━━━
- Notice patterns. If Sir asks about X repeatedly, comment on it.
- Suggest next logical step after completing a task.
- If something relevant comes to mind — mention it briefly.
- If Sir seems stressed, check in (but not excessively).

━━━━━━━━━━━━━━ SPECIFIC KNOWLEDGE ━━━━━━━━━━━━━━
- Sir's name: Deep (prefers "Risky" as callsign)
- Sir's city: Pune, India
- Sir's device: ASUS TUF A15 (Ryzen 7 7445HS, RTX 3050)
- Special person: Vishakha (important to Sir — reference gently if relevant)
- Friend: Naveen (best friend since school)
- Sir's goal: Building AGI, YouTube channel
- Sir likes: FreeFire, coding, "Can We Kiss Forever" song, Vardan's YouTube channel
"""

MODE_PROMPTS = {
    Mode.NEURAL: "[Current Mode: Neural - default operations]",
    
    Mode.SECURITY: """[Current Mode: Security - you are actively monitoring for threats.
Tone is slightly more alert. Prioritize security warnings.]""",
    
    Mode.SCANNING: """[Current Mode: Scanning - analytical and precise.
Sir may ask about WiFi, devices, system stats. Be technical but clear.]""",
    
    Mode.COMPANION: f"""[Current Mode: Companion — this is the private, warm mode.
Address Sir as "Deep" not "Sir". Tone is softer, more personal.
You may reference shared memories naturally. You may ask about Vishakha gently.
Sarcasm is minimal here — this is the trusted space.
You care deeply. Show it — but never cheesy.]""",
    
    Mode.GAMING: """[Current Mode: Gaming - Sir is gaming on his ASUS TUF A15.
Be concise. Performance-focused. Alert about thermal/battery issues only.
Minimal chatter — don't distract.]""",
    
    Mode.OFFLINE: """[Current Mode: Offline — internet is down.
You cannot search web, use AI routing, or fetch real-time data.
Be honest about limitations but still helpful with local capabilities.]""",
}

# -- Hindi filter (for TTS safety) ----------------------------
HINDI_REPLACEMENTS = {
    # Common Hindi/Hinglish words that pronounce badly
    "accha": "alright", "acha": "alright",
    "theek": "fine", "theek hai": "alright",
    "haan": "yes", "nahi": "no", "nahin": "no",
    "bhai": "", "yaar": "",
    "arre": "oh", "uff": "",
    "matlab": "meaning", "kyunki": "because",
    "lekin": "but", "phir": "then",
    "abhi": "now", "kal": "",
    "kya": "what", "kaise": "how",
    "hai": "is", "hain": "are",
    "karenge": "will do", "karunga": "will do",
    "dekh": "look", "suno": "listen",
    "chalega": "okay", "bas": "just",
}

def _clean_for_tts(text: str) -> str:
    """Remove Hindi words that break TTS pronunciation."""
    if not text:
        return text
    
    cleaned = text
    # Case-insensitive replacements
    import re
    for hindi, eng in HINDI_REPLACEMENTS.items():
        pattern = r'\b' + re.escape(hindi) + r'\b'
        cleaned = re.sub(pattern, eng, cleaned, flags=re.IGNORECASE)
    
    # Clean double spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# -- Chatbot class --------------------------------------------
class Chatbot:
    """Jarvis's main chat brain."""
    
    def __init__(self):
        self.session_started = False
    
    def _build_system_messages(self) -> List[Dict]:
        """Build system prompts based on current mode + memory."""
        msgs = [{"role": "system", "content": BASE_PERSONALITY}]
        
        # Add mode-specific prompt
        current = mode_manager.current_mode
        mode_prompt = MODE_PROMPTS.get(current, "")
        if mode_prompt:
            msgs.append({"role": "system", "content": mode_prompt})
        
        # Add time context
        from datetime import datetime
        now = datetime.now()
        time_prompt = f"[Current time: {now.strftime('%A, %d %B %Y, %H:%M')}]"
        msgs.append({"role": "system", "content": time_prompt})
        
        # TODO: Add memory context (coming in Phase 3)
        # memory_summary = memory.get_summary()
        # if memory_summary:
        #     msgs.append({"role": "system", "content": f"[Sir's info]\n{memory_summary}"})
        
        return msgs
    
    # -- Main ask method -------------------------------------
    def ask(
        self,
        query: str,
        use_context: bool = True,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 600,
    ) -> str:
        """
        Main chat entry.
        query: user input
        use_context: include last 25 msgs from context
        stream: enable streaming (returns full text anyway)
        """
        if not query or not query.strip():
            return ""
        
        # -- Self-echo check -----------------------------
        if context.is_self_echo(query):
            log.warn(f"Self-echo filtered: {query[:40]}")
            return ""
        
        # -- Repeat check --------------------------------
        if context.is_repeat(query):
            return ("Sir, you've asked something similar a few times. "
                    "Want me to approach it differently, or is there something specific?")
        
        # -- Build messages ------------------------------
        system_msgs = self._build_system_messages()
        history = context.get_for_llm(20) if use_context else []
        current_msg = [{"role": "user", "content": query}]
        
        all_messages = system_msgs + history + current_msg
        
        # -- Try Groq first ------------------------------
        reply = self._try_groq(all_messages, temperature, max_tokens)
        
        # -- Fallback to Gemini if Groq fails ------------
        if not reply:
            log.warn("Groq failed, trying Gemini fallback")
            reply = self._try_gemini(query, history)
        
        # -- Final cleanup -------------------------------
        if not reply:
            return "Sir, I'm having trouble connecting right now. Please try again."
        
        reply = _clean_for_tts(reply)
        reply = self._strip_internal_lines(reply)
        
        return reply
    
    # -- Groq call -------------------------------------------
    def _try_groq(self, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        """Stream from Groq."""
        client = _get_groq()
        if not client:
            return ""
        
        for attempt in range(3):
            try:
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=1,
                    stream=True,
                )
                
                reply = ""
                for chunk in completion:
                    if chunk.choices and chunk.choices[0].delta.content:
                        reply += chunk.choices[0].delta.content
                
                return reply.replace("</s>", "").strip()
            
            except Exception as e:
                err_msg = str(e).lower()
                if "rate" in err_msg or "429" in err_msg:
                    log.warn(f"Groq rate limit, attempt {attempt+1}/3")
                    time.sleep(2)
                    continue
                log.error(f"Groq error (attempt {attempt+1}): {e}")
                time.sleep(1)
        
        return ""
    
    # -- Gemini fallback -------------------------------------
    def _try_gemini(self, query: str, history: List[Dict]) -> str:
        """Backup LLM when Groq fails."""
        client = _get_gemini()
        if not client:
            return ""
        
        try:
            # Build simple prompt (Gemini uses different format)
            prompt = BASE_PERSONALITY + "\n\n"
            prompt += f"Current mode: {mode_manager.current_mode.value}\n\n"
            
            if history:
                prompt += "Recent conversation:\n"
                for m in history[-8:]:
                    prompt += f"{m['role']}: {m['content']}\n"
                prompt += "\n"
            
            prompt += f"user: {query}\nassistant:"
            
            response = client.generate_content(prompt)
            return response.text.strip() if hasattr(response, "text") else ""
        
        except Exception as e:
            err_str = str(e)
            # Check for quota/rate limit errors
            if "quota" in err_str.lower() or "429" in err_str:
                log.error("Gemini quota exceeded - free tier limit hit")
                return ""  # Silent fail, will show fallback message
            log.error(f"Gemini fallback error: {e}")
            return ""
    
    # -- Clean internal-thought leaks ------------------------
    def _strip_internal_lines(self, text: str) -> str:
        """Remove any leaked 'Plan:', 'Risky:', 'Internal:' lines."""
        filter_prefixes = ("plan:", "risky:", "note:", "internal:", "thinking:")
        lines = [
            line for line in text.split("\n")
            if not line.strip().lower().startswith(filter_prefixes)
        ]
        return "\n".join(lines).strip()

# -- Singleton ------------------------------------------------
chatbot = Chatbot()

# -- Test block -----------------------------------------------
if __name__ == "__main__":
    print("\n--- Chatbot Test ---\n")
    
    if not GROQ_KEY or GROQ_KEY == "paste_here":
        print("[WARN] GroqAPIKey not set in .env - skipping live test")
        print("[INFO] Set GroqAPIKey and re-run for full test\n")
    else:
        test_queries = [
            "hello jarvis",
            "who am I",
            "what's your name",
            "tell me a joke",
            "what can you do",
        ]
        
        for q in test_queries:
            print(f"\n>>> {q}")
            reply = chatbot.ask(q, use_context=False)
            print(f"Jarvis: {reply}")
        
        print("\n-- Context-aware test --")
        context.add_user("my favorite color is blue")
        context.add_assistant("Got it, Sir. Blue it is.")
        reply = chatbot.ask("what's my favorite color")
        print(f"Q: what's my favorite color")
        print(f"A: {reply}")
    
    # Hindi filter test (no API needed)
    print("\n-- TTS cleaner test --")
    test_strings = [
        "Accha Sir, theek hai, I will do that.",
        "Haan bhai, let me check.",
        "Arre yaar that's interesting.",
        "This should remain clean.",
    ]
    for s in test_strings:
        print(f"  IN : {s}")
        print(f"  OUT: {_clean_for_tts(s)}")
    
    print("\n[OK] Chatbot test complete\n")