import re
from datetime import datetime, timedelta, timezone
from core.llm_manager import get_aries_response
from tools.search_module import fetch_live_info
from tools.calendar_module import (
    get_upcoming_events,
    get_today_events,
    reschedule_next_event,
    create_event,
    suggest_reschedule_slot,
    suggest_reschedule_slots,
    confirm_reschedule,
)
import core.state as state

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────
#  STATE — all reads/writes go through core.state
#  so main.py and brain.py share the same object
# ─────────────────────────────────────────────
# Local-only state (not needed outside brain)
PENDING_DATA      = {}
PENDING_RESCHEDULE = {}

# ─────────────────────────────────────────────
#  STT CLEANUP
# ─────────────────────────────────────────────
TIME_REGEX = r'(\d{1,2})(?:[:\.](\d{2}))?\s*([ap]\.?m?\.?)'

# Word → digit map for spoken numbers STT often returns
_WORD_TO_NUM = {
    'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
    'eleven': '11', 'twelve': '12'
}

def clean_stt_shorthand(text: str) -> str:
    # Convert spoken number words to digits (e.g. "five pm" -> "5 pm")
    for word, digit in _WORD_TO_NUM.items():
        text = re.sub(rf'\b{word}\b', digit, text, flags=re.IGNORECASE)

    # Fix bare 3-4 digit times run together e.g. "145 pm" -> "1:45 pm", "1230pm" -> "12:30 pm"
    # Must run BEFORE the lone-p/a fix so "145pm" doesn't get mangled
    def split_bare_time(m):
        digits = m.group(1)
        suffix = m.group(2)
        if len(digits) == 3:          # 145 -> 1:45
            return f"{digits[0]}:{digits[1:]} {suffix}"
        elif len(digits) == 4:        # 1230 -> 12:30
            return f"{digits[:2]}:{digits[2:]} {suffix}"
        return m.group(0)             # leave as-is

    text = re.sub(r'\b(\d{3,4})\s*(am|pm)\b', split_bare_time, text, flags=re.IGNORECASE)

    # Fix "5bmm", "5bm" STT glitches -> "5 pm"
    text = re.sub(r'(\d+)\s*b[mp]m?\b', r'\1 pm', text)
    text = re.sub(r'(\d+)\s*bam?\b', r'\1 am', text)

    # Fix trailing lone 'p' or 'a' after digit (e.g. "5 p" -> "5 pm")
    text = re.sub(r'(\d+)\s*p\b', r'\1 pm', text)
    text = re.sub(r'(\d+)\s*a\b', r'\1 am', text)

    # Normalise punctuated forms
    text = text.replace('p.m.', 'pm').replace('a.m.', 'am')

    # Strip filler words STT inserts before times ("large", "uh", "um" etc.)
    text = re.sub(r'\b(large|like|uh|um|er)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    return text

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
RESCHEDULE_TRIGGERS = [
    "reschedule", "move it", "change it", "can't make it", "cannot make it",
    "won't make it", "not going to make it", "shift it", "push it",
    "i can't attend", "cancel and reschedule", "find another time",
    "find a new time", "different time", "another slot"
]

CONFIRM_WORDS  = ["yes", "fine", "ok", "sure", "yep", "confirm", "do it", "go ahead", "sounds good"]
DECLINE_WORDS  = ["no", "don't", "cancel", "stop", "never mind", "skip"]


def _is_reschedule_intent(text: str) -> bool:
    # Exact trigger words
    if any(trigger in text for trigger in RESCHEDULE_TRIGGERS):
        return True
    # Fuzzy match — STT often garbles "reschedule" into reshade/reshadee/reschedule/rishudule etc.
    if re.search(r're\s?s[hc][aeiou]?[dj][ue]?[eol]?', text, re.IGNORECASE):
        return True
    return False


def _reset_state():
    global PENDING_DATA, PENDING_RESCHEDULE
    state.last_intent         = None
    state.current_alert_event = None
    PENDING_DATA              = {}
    PENDING_RESCHEDULE        = {}


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────
def process_text(user_text: str) -> str:
    global PENDING_DATA, PENDING_RESCHEDULE

    user_text = clean_stt_shorthand(user_text)
    text = user_text.lower().strip()

    if not text:
        return "I am standing by, Sir."

    now = datetime.now(IST)

    # ══════════════════════════════════════════
    #  STEP 1 — USER PICKING A SLOT FROM THE LIST
    # ══════════════════════════════════════════
    if state.last_intent == "AWAITING_RESCHEDULE_CONFIRM" and PENDING_RESCHEDULE:
        event = PENDING_RESCHEDULE.get('event')
        slots = PENDING_RESCHEDULE.get('slots', [])  # list of (start, end, label)

        # Check if user said a number — "option 2", "second", "number 3", just "2" etc.
        slot_pick = None
        num_match = re.search(r'\b([1-5]|one|two|three|four|five|first|second|third|fourth|fifth)\b', text)
        word_to_idx = {'one': 0, 'first': 0, 'two': 1, 'second': 1,
                       'three': 2, 'third': 2, 'four': 3, 'fourth': 3,
                       'five': 4, 'fifth': 4}
        if num_match:
            raw = num_match.group(1)
            if raw.isdigit():
                slot_pick = int(raw) - 1
            else:
                slot_pick = word_to_idx.get(raw.lower())

        if slot_pick is not None and slots and 0 <= slot_pick < len(slots):
            new_start, new_end, _ = slots[slot_pick]
            _reset_state()
            return confirm_reschedule(event, new_start, new_end)

        # Decline — keep as is
        if any(w in text for w in DECLINE_WORDS) and not any(w in text for w in CONFIRM_WORDS):
            _reset_state()
            return "Understood, Sir. The meeting stays as it is."

        # If slots exist, re-prompt
        if slots:
            lines = [f"Option {i+1}: {lbl} at {s.strftime('%I:%M %p')}" for i, (s, e, lbl) in enumerate(slots)]
            return "Please pick an option, Sir: " + ", ".join(lines) + "."

        return "Sir, which slot would you prefer?"

    # ══════════════════════════════════════════
    #  STEP 2 — ALERT CONFIRMATION
    # ══════════════════════════════════════════
    if state.last_intent == "CONFIRMING_ALERT" and state.current_alert_event:
        event = state.current_alert_event

        # Detect reschedule intent FIRST before checking confirm words
        # so "yes reschedule it" routes correctly
        wants_reschedule = _is_reschedule_intent(text) or any(
            w in text for w in ["no", "move", "change", "not fine", "reschedule"]
        )
        wants_confirm = any(w in text for w in CONFIRM_WORDS) and not wants_reschedule

        if wants_confirm:
            title = event.get('summary', 'the meeting')
            _reset_state()
            return f"Excellent, Sir. '{title}' is confirmed. I'll keep it in the schedule."

        # Everything else → show all available slots
        msg, slots = suggest_reschedule_slots(event, count=5)
        if slots:
            state.last_intent         = "AWAITING_RESCHEDULE_CONFIRM"
            state.current_alert_event = None
            PENDING_RESCHEDULE        = {'event': event, 'slots': slots}
        else:
            _reset_state()
        return msg

    # ══════════════════════════════════════════
    #  STEP 3 — CONTEXT RECOVERY (awaiting time)
    # ══════════════════════════════════════════
    if state.last_intent == "AWAITING_TIME" and re.search(TIME_REGEX, text):
        saved_title = PENDING_DATA.get('title', 'Meeting')
        text = f"schedule {saved_title} " + text
        if PENDING_DATA.get('day') == 'tomorrow':
            text += " tomorrow"
    elif state.last_intent == "AWAITING_TIME" and not re.search(TIME_REGEX, text):
        state.last_intent = None
        PENDING_DATA = {}

    # ══════════════════════════════════════════
    #  STEP 4 — STANDALONE RESCHEDULE INTENT
    # ══════════════════════════════════════════
    if _is_reschedule_intent(text):
        target_event = None
        events = get_upcoming_events(10)

        for e in events:
            summary = e.get('summary', '').lower()
            if summary and summary in text:
                target_event = e
                break

        if not target_event:
            times_found = re.findall(TIME_REGEX, text)
            if times_found:
                t = times_found[0]
                hour = int(t[0])
                minute = int(t[1]) if t[1] else 0
                meridiem = t[2].replace('.', '').lower()
                if 'p' in meridiem and hour < 12: hour += 12
                if 'a' in meridiem and hour == 12: hour = 0
                for e in events:
                    s_str = e['start'].get('dateTime')
                    if s_str:
                        s_dt = datetime.fromisoformat(s_str.replace('Z', '+00:00')).astimezone(IST)
                        if s_dt.hour == hour and abs(s_dt.minute - minute) <= 5:
                            target_event = e
                            break

        if not target_event and events:
            target_event = events[0]

        if not target_event:
            return "I don't see any upcoming events to reschedule, Sir."

        suggestion, slots = suggest_reschedule_slots(target_event, count=5)
        if slots:
            state.last_intent         = "AWAITING_RESCHEDULE_CONFIRM"
            state.current_alert_event = None
            PENDING_RESCHEDULE        = {'event': target_event, 'slots': slots}

        return suggestion

    # ══════════════════════════════════════════
    #  STEP 5 — CALENDAR READING
    # ══════════════════════════════════════════
    if any(w in text for w in ["schedule", "schedules", "agenda", "plans", "events", "meetings"]) \
       and any(q in text for q in ["what", "show", "list", "check", "tell"]):

        state.last_intent = None
        if "today" in text:
            start = now
            end = now.replace(hour=23, minute=59, second=59)
            label = "today"
        elif "tomorrow" in text:
            tomorrow = now + timedelta(days=1)
            start = tomorrow.replace(hour=0, minute=0, second=0)
            end = tomorrow.replace(hour=23, minute=59, second=59)
            label = "tomorrow"
        else:
            start = now
            end = None
            label = "upcoming"

        events = get_upcoming_events(
            10,
            timeMin=start.isoformat(),
            timeMax=end.isoformat() if end else None
        )

        if not events:
            return f"You have no upcoming events for {label}, Sir."

        result = []
        for e in events:
            start_str = e['start'].get('dateTime') or e['start'].get('date')
            try:
                dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(IST)
            except Exception:
                dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=IST)

            if label != "upcoming" and dt.date() != start.date():
                continue
            if label == "today" and dt < now:
                continue

            time_str = "all day" if 'date' in e['start'] else dt.strftime('%I:%M %p')
            result.append(f"{e.get('summary', 'event')} at {time_str}")

        if not result:
            return f"No further events for {label}, Sir."

        return f"Your schedule for {label}: " + ", ".join(result) + "."

    # ══════════════════════════════════════════
    #  STEP 6 — SCHEDULING
    # ══════════════════════════════════════════
    if any(word in text for word in ["schedule", "add a", "set an", "have a"]):
        try:
            target_date = now.date()
            day_context = "today"
            label = f"today, {target_date.strftime('%d %B')}"

            if "tomorrow" in text:
                target_date = (now + timedelta(days=1)).date()
                label = f"tomorrow, {target_date.strftime('%d %B')}"
                day_context = "tomorrow"

            times = re.findall(TIME_REGEX, text)

            title_match = re.search(
                r'(?:have|schedule|add|set)\s+(?:a|an|my)?\s*(.*?)\s*(?:for|at|on|tomorrow|today|$)',
                text
            )
            extracted_title = title_match.group(1).strip() if title_match else "Meeting"
            if extracted_title.lower() in ["it", "this", "something", ""]:
                extracted_title = "Meeting"
            extracted_title = re.sub(TIME_REGEX, '', extracted_title).strip(" ?.").capitalize()

            with_match = re.search(r'meeting\s+with\s+([a-zA-Z\s]+?)(?:\s+(?:at|for|on|today|tomorrow)|$)', text)
            if with_match:
                name = with_match.group(1).strip().title()
                extracted_title = f"Meeting with {name}"

            if not times:
                state.last_intent = "AWAITING_TIME"
                PENDING_DATA = {"title": extracted_title, "day": day_context}
                return f"At what time should I schedule your '{extracted_title}' for {day_context}, Sir?"

            def convert(t):
                hour     = int(t[0])
                minute   = int(t[1]) if t[1] else 0
                meridiem = t[2].replace('.', '').lower()
                if 'p' in meridiem and hour < 12: hour += 12
                if 'a' in meridiem and hour == 12: hour = 0
                return hour, minute

            sh, sm = convert(times[0])
            start_dt = datetime.combine(
                target_date,
                datetime.min.time().replace(hour=sh, minute=sm)
            ).replace(tzinfo=IST)

            if "to" in text and len(times) >= 2:
                eh, em = convert(times[1])
                end_dt = datetime.combine(
                    target_date,
                    datetime.min.time().replace(hour=eh, minute=em)
                ).replace(tzinfo=IST)
            else:
                dur_match = re.search(r'for (\d+)', text)
                duration  = int(dur_match.group(1)) if dur_match else 1
                end_dt    = start_dt + timedelta(hours=duration)

            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)

            success = create_event(extracted_title, start_dt.isoformat(), end_dt.isoformat())

            if success:
                state.last_intent = None
                PENDING_DATA = {}
                return f"Done, Sir. I've scheduled '{extracted_title}' for {label} at {start_dt.strftime('%I:%M %p')}."

            return "The calendar uplink failed, Sir."

        except Exception as e:
            print(f"Scheduling Error: {e}")
            state.last_intent = None
            return "I encountered a logic error while scheduling, Sir."

    # ══════════════════════════════════════════
    #  STEP 7 — QUICK RESPONSES
    # ══════════════════════════════════════════
    if re.search(r'\btime\b', text):
        return f"The current time is {datetime.now(IST).strftime('%I:%M %p')}."

    if "how are you" in text:
        return "All systems are nominal. Logic processors are primed."

    # ══════════════════════════════════════════
    #  STEP 8 — LLM FALLBACK
    #  Only reaches here if no intent is active
    # ══════════════════════════════════════════
    if state.last_intent != "CONFIRMING_ALERT":
        state.last_intent = None

    response = get_aries_response(user_text)

    if response.strip().upper().startswith("SEARCH"):
        try:
            query    = response.split("SEARCH", 1)[-1].strip(" []:")
            web_data = fetch_live_info(query)
            refined  = f"Found info: {web_data}\nUser asked: {user_text}\nAnswer wittily."
            return get_aries_response(refined)
        except Exception:
            return "Web search failed. Relying on local core."

    return response


# ─────────────────────────────────────────────
#  CALLED BY main.py when alert fires
# ─────────────────────────────────────────────
def handle_alert_event(event: dict) -> str:
    """Sets shared state and returns the spoken alert message."""
    state.last_intent         = "CONFIRMING_ALERT"
    state.current_alert_event = event

    name      = event.get('summary', 'a meeting')
    start_str = event['start'].get('dateTime') or event['start'].get('date')
    start_dt  = datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(IST)
    time_str  = start_dt.strftime('%I:%M %p')

    return (
        f"Sir, you have '{name}' coming up at {time_str}. "
        "Are we proceeding as planned, or shall I reschedule it?"
    )