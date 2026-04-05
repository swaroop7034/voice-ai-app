import asyncio
import re
from datetime import datetime, timedelta, timezone
import ollama
from core.llm_manager import get_aries_response, get_recent_chat_history
from core.memory_classifier import classify_and_store_background
from core.behavior_analyzer import build_behavior_context
from core.user_profile import get_user_profile, update_user_profile, format_profile_section
from core.intelligence_layer import (
    MAX_HISTORY,
    MAX_MEMORY,
    apply_memory_decay,
    filter_memories_by_type,
    smart_memory_selector,
    compress_context,
)
from tools.search_module import fetch_live_info
from tools.calendar_module import (
    check_conflict,
    get_upcoming_events,
    get_today_events,
    reschedule_next_event,
    create_event,
    rename_event_title,
    suggest_conflict_free_slots,
    suggest_reschedule_slot,
    suggest_reschedule_slots,
    confirm_reschedule,
)
import core.state as state
from integrations.supabase_store import build_memory_context, get_default_user_id, log_interaction, search_similar
from logger import logger, log_error, log_step

IST = timezone(timedelta(hours=5, minutes=30))
FLOW_TIMEOUT_SECONDS = 120

# ─────────────────────────────────────────────
#  STATE — all reads/writes go through core.state
#  so main.py and brain.py share the same object
# ─────────────────────────────────────────────
# Local-only state (not needed outside brain)
PENDING_DATA      = {}
PENDING_RESCHEDULE = {}
PENDING_CONFLICT = {}
USER_FLOW_STATE: dict[str, dict[str, object]] = {}

# ─────────────────────────────────────────────
#  STT CLEANUP
# ─────────────────────────────────────────────
TIME_REGEX = r'(\d{1,2})(?:[:\.](\d{2}))?\s*([ap]\.?m?\.?)'
LOOSE_TIME_REGEX = r'\b(\d{1,2})(?:[:\.](\d{2}))?\b'
TIME_QUERY_REGEX = r'\b(what\s+(?:is|s\s+)?(?:the\s+)?time|current\s+time|tell\s+me\s+the\s+time|what\'s\s+the\s+time)\b'

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
SCHEDULING_FOLLOWUP_KEYWORDS = [
    "reschedule", "change", "move", "make it", "instead", "cancel", "yes", "confirm",
]
RENAME_FOLLOWUP_KEYWORDS = [
    "rename", "retitle", "change", "call it", "name it", "today", "tomorrow", "instead",
]

SCHEDULE_KEYWORDS = ["schedule", "add", "set", "book", "create", "plan", "meeting", "appointment"]
RESCHEDULE_KEYWORDS = [
    "reschedule", "move", "shift", "change", "postpone", "another slot", "different time", "can't make",
]
RENAME_KEYWORDS = ["rename", "retitle", "change title", "change name", "call it", "name it"]
CANCEL_KEYWORDS = ["cancel", "drop", "remove", "delete", "call off"]
QUERY_EVENT_KEYWORDS = ["agenda", "events", "meeting", "schedule", "plans"]
QUERY_VERBS = ["what", "show", "list", "check", "tell"]


def _is_reschedule_intent(text: str) -> bool:
    # Exact trigger words
    if any(trigger in text for trigger in RESCHEDULE_TRIGGERS):
        return True
    # Fuzzy match — STT often garbles "reschedule" into reshade/reshadee/reschedule/rishudule etc.
    if re.search(r're\s?s[hc][aeiou]?[dj][ue]?[eol]?', text, re.IGNORECASE):
        return True
    return False


def classify_intent(text: str) -> str:
    """Rule-first classifier returning schedule|reschedule|cancel|query_event|general|uncertain."""
    txt = text.lower().strip()

    reschedule_hits = sum(1 for keyword in RESCHEDULE_KEYWORDS if keyword in txt)
    if _is_reschedule_intent(txt):
        reschedule_hits += 2

    rename_hits = sum(1 for keyword in RENAME_KEYWORDS if keyword in txt)
    cancel_hits = sum(1 for keyword in CANCEL_KEYWORDS if keyword in txt)
    schedule_hits = sum(1 for keyword in SCHEDULE_KEYWORDS if keyword in txt)
    query_hits = sum(1 for keyword in QUERY_EVENT_KEYWORDS if keyword in txt)
    query_verb_hits = sum(1 for keyword in QUERY_VERBS if keyword in txt)
    has_time = bool(re.search(TIME_REGEX, txt))

    if reschedule_hits >= 2:
        return "reschedule"
    if rename_hits >= 1 and schedule_hits >= 1:
        return "rename"
    if rename_hits >= 1:
        return "rename"
    if cancel_hits >= 2 and reschedule_hits == 0:
        return "cancel"
    if query_hits >= 1 and query_verb_hits >= 1 and not has_time:
        return "query_event"
    if schedule_hits >= 2 and (has_time or "tomorrow" in txt or "today" in txt):
        return "schedule"
    # If user is clearly asking to schedule but omitted explicit time/day, enter scheduling flow.
    if schedule_hits >= 1 and query_verb_hits == 0 and cancel_hits == 0 and reschedule_hits == 0:
        return "schedule"

    # Weak/ambiguous calendar language should be resolved by LLM fallback.
    weak_calendar_signal = (schedule_hits + query_hits + cancel_hits + reschedule_hits) >= 1
    if weak_calendar_signal:
        return "uncertain"

    return "general"


async def classify_intent_with_llm(text: str) -> str:
    prompt = (
        "Classify this request into one of [schedule, reschedule, rename, cancel, general]. "
        "Return only one word.\n"
        f"Request: {text}"
    )
    try:
        response = await asyncio.to_thread(
            ollama.generate,
            model="phi3",
            prompt=prompt,
            stream=False,
        )
        label = str(response.get("response", "general")).strip().lower()
        label = re.sub(r"[^a-z_]", "", label)
        if label in {"schedule", "reschedule", "rename", "cancel", "general"}:
            return label
        return "general"
    except Exception as exc:
        log_error(f"INTENT LLM fallback failed: {exc}")
        return "general"


def _reset_state():
    global PENDING_DATA, PENDING_RESCHEDULE, PENDING_CONFLICT
    state.last_intent         = None
    state.current_alert_event = None
    PENDING_DATA              = {}
    PENDING_RESCHEDULE        = {}
    PENDING_CONFLICT          = {}


def _get_user_flow_state(user_id: str) -> dict[str, object]:
    return USER_FLOW_STATE.setdefault(
        user_id,
        {
            "active_flow": None,
            "flow_data": {},
            "last_updated": datetime.now(IST),
        },
    )


def _start_scheduling_flow(user_id: str, flow_data: dict[str, object] | None = None) -> None:
    flow_state = _get_user_flow_state(user_id)
    if flow_state.get("active_flow") != "scheduling":
        log_step("FLOW_STARTED_SCHEDULING")
    flow_state["active_flow"] = "scheduling"
    if flow_data is not None:
        flow_state["flow_data"] = flow_data
    flow_state["last_updated"] = datetime.now(IST)


def _start_rename_flow(user_id: str, flow_data: dict[str, object] | None = None) -> None:
    flow_state = _get_user_flow_state(user_id)
    if flow_state.get("active_flow") != "rename":
        log_step("FLOW_STARTED_RENAME")
    flow_state["active_flow"] = "rename"
    if flow_data is not None:
        flow_state["flow_data"] = flow_data
    flow_state["last_updated"] = datetime.now(IST)


def _update_scheduling_flow_data(user_id: str, updates: dict[str, object]) -> None:
    flow_state = _get_user_flow_state(user_id)
    existing = dict(flow_state.get("flow_data") or {})
    existing.update(updates)
    flow_state["flow_data"] = existing
    flow_state["last_updated"] = datetime.now(IST)


def _end_scheduling_flow(user_id: str) -> None:
    flow_state = _get_user_flow_state(user_id)
    if flow_state.get("active_flow") is not None:
        log_step("FLOW_ENDED")
    flow_state["active_flow"] = None
    flow_state["flow_data"] = {}
    flow_state["last_updated"] = datetime.now(IST)


def _is_scheduling_followup_text(text: str) -> bool:
    if _extract_time_tokens(text):
        return True
    return any(keyword in text for keyword in SCHEDULING_FOLLOWUP_KEYWORDS)


def _is_rename_followup_text(text: str) -> bool:
    if _extract_time_tokens(text):
        return True
    return any(keyword in text for keyword in RENAME_FOLLOWUP_KEYWORDS)


def _is_explicit_cancel_request(text: str) -> bool:
    return bool(re.search(r"\b(cancel|stop|skip|dont|don't)\b|never\s+mind", text, flags=re.IGNORECASE))


def _extract_time_tokens(text: str) -> list[tuple[str, str, str]]:
    strict_matches = re.findall(TIME_REGEX, text)
    if strict_matches:
        return [(str(h), str(m), str(ap)) for h, m, ap in strict_matches]

    # Loose fallback for replies like "5" or "17:30" when ARIS is awaiting time.
    loose_matches = re.findall(LOOSE_TIME_REGEX, text)
    tokens: list[tuple[str, str, str]] = []
    for hour_raw, minute_raw in loose_matches:
        try:
            hour = int(hour_raw)
        except Exception:
            continue
        if 0 <= hour <= 23:
            tokens.append((str(hour_raw), str(minute_raw or ""), ""))
    return tokens


def _convert_time_token(token: tuple[str, str, str], require_meridiem: bool = True) -> tuple[int, int]:
    hour = int(token[0])
    minute = int(token[1]) if token[1] else 0
    meridiem = token[2].replace('.', '').lower() if token[2] else ""

    if not meridiem:
        if require_meridiem and hour <= 12:
            raise ValueError("Ambiguous time without AM/PM")
        return hour, minute

    if 'p' in meridiem and hour < 12:
        hour += 12
    if 'a' in meridiem and hour == 12:
        hour = 0
    return hour, minute


def _format_slot_options(slots: list[tuple[datetime, datetime, str]]) -> str:
    lines = [f"{i + 1}. {label} at {start.strftime('%I:%M %p')}" for i, (start, _end, label) in enumerate(slots)]
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────
async def process_text(user_text: str, user_id: str | None = None) -> str:
    global PENDING_DATA, PENDING_RESCHEDULE, PENDING_CONFLICT

    resolved_user_id = user_id or get_default_user_id()
    user_text = clean_stt_shorthand(user_text)
    text = user_text.lower().strip()

    if not text:
        return "I am standing by, Sir."

    now = datetime.now(IST)
    forced_intent: str | None = None
    scheduling_timed_out = False

    flow_state = _get_user_flow_state(resolved_user_id)
    active_flow = flow_state.get("active_flow")
    last_updated = flow_state.get("last_updated")
    if isinstance(last_updated, datetime) and (now - last_updated).total_seconds() > FLOW_TIMEOUT_SECONDS:
        if active_flow in {"scheduling", "rename"}:
            log_step("FLOW_TIMEOUT")
            _reset_state()
            _end_scheduling_flow(resolved_user_id)
            active_flow = None
            scheduling_timed_out = True

    if scheduling_timed_out and _is_scheduling_followup_text(text) and not any(k in text for k in SCHEDULE_KEYWORDS):
        return (
            "Your scheduling session timed out, Sir. "
            "Please start again with the full request, for example: schedule a meeting at 4 PM."
        )

    if scheduling_timed_out and _is_rename_followup_text(text) and "rename" not in text and "retitle" not in text:
        return (
            "Your rename session timed out, Sir. "
            "Please restate the request, for example: rename my 10 AM meeting to Project Review."
        )

    if active_flow == "scheduling":
        flow_state["last_updated"] = now

        if _is_explicit_cancel_request(text):
            _reset_state()
            _end_scheduling_flow(resolved_user_id)
            return "Scheduling cancelled, Sir."

        # During scheduling flow, treat follow-ups as scheduling input even if classifier is uncertain.
        if _is_scheduling_followup_text(text):
            log_step("SCHEDULING_FOLLOWUP_HANDLED")
            flow_data = dict(flow_state.get("flow_data") or {})
            flow_title = str(flow_data.get("title") or PENDING_DATA.get("title") or "Meeting")
            flow_day = str(flow_data.get("day") or PENDING_DATA.get("day") or "today")

            if state.last_intent not in {"AWAITING_RESCHEDULE_CONFIRM", "AWAITING_CONFLICT_RESOLUTION"}:
                if _extract_time_tokens(text) and "schedule" not in text:
                    text = f"schedule {flow_title} {text}"
                elif any(token in text for token in ["make it", "change", "move", "instead", "reschedule"]):
                    text = f"schedule {flow_title} {text}"

                if "tomorrow" in text:
                    flow_day = "tomorrow"
                elif "today" in text:
                    flow_day = "today"
                _update_scheduling_flow_data(resolved_user_id, {"title": flow_title, "day": flow_day})
                forced_intent = "schedule"

    if active_flow == "rename":
        flow_state["last_updated"] = now

        if _is_explicit_cancel_request(text):
            _end_scheduling_flow(resolved_user_id)
            return "Rename cancelled, Sir."

        if _is_rename_followup_text(text):
            log_step("RENAME_FOLLOWUP_HANDLED")
            flow_data = dict(flow_state.get("flow_data") or {})
            flow_new_title = str(flow_data.get("new_title") or "").strip()
            flow_old_hint = str(flow_data.get("old_title_hint") or "").strip()

            if "to " not in text and " as " not in text and flow_new_title:
                base = text
                if _extract_time_tokens(base):
                    time_text = _extract_time_tokens(base)[0]
                    inferred_time = f"{time_text[0]}:{(time_text[1] or '00')} {time_text[2]}".strip()
                    text = f"rename {flow_old_hint or 'meeting'} {inferred_time} to {flow_new_title}"
                else:
                    text = f"rename {flow_old_hint or 'meeting'} {base} to {flow_new_title}"

            forced_intent = "rename"
        else:
            return "I am still in rename mode, Sir. Tell me the corrected day/time or the new title."

    # ══════════════════════════════════════════
    #  STEP 0 — CONFLICT-RESOLUTION FOLLOW-UP
    # ══════════════════════════════════════════
    if state.last_intent == "AWAITING_CONFLICT_RESOLUTION" and PENDING_CONFLICT:
        slots = PENDING_CONFLICT.get("slots", [])
        title = str(PENDING_CONFLICT.get("title") or "Meeting")
        duration_minutes = int(PENDING_CONFLICT.get("duration_minutes") or 60)
        duration = timedelta(minutes=duration_minutes)

        if any(w in text for w in DECLINE_WORDS):
            _reset_state()
            _end_scheduling_flow(resolved_user_id)
            return "Understood, Sir. I will not schedule that event."

        selected_slot = None
        num_match = re.search(r'\b([1-5]|one|two|three|four|five|first|second|third|fourth|fifth)\b', text)
        word_to_idx = {
            'one': 0, 'first': 0, 'two': 1, 'second': 1,
            'three': 2, 'third': 2, 'four': 3, 'fourth': 3,
            'five': 4, 'fifth': 4,
        }
        if num_match and slots:
            raw = num_match.group(1)
            slot_idx = int(raw) - 1 if raw.isdigit() else word_to_idx.get(raw.lower(), -1)
            if 0 <= slot_idx < len(slots):
                selected_slot = slots[slot_idx]

        if selected_slot is None:
            picked_times = _extract_time_tokens(text)
            if picked_times:
                try:
                    hour, minute = _convert_time_token(picked_times[0], require_meridiem=False)
                    for start_dt, end_dt, label in slots:
                        if start_dt.hour == hour and start_dt.minute == minute:
                            selected_slot = (start_dt, end_dt, label)
                            break
                except Exception:
                    selected_slot = None

        if selected_slot is None:
            if slots:
                return (
                    "Please choose one of these available slots, Sir:\n"
                    f"{_format_slot_options(slots)}"
                )
            _reset_state()
            return "I could not find available slots anymore, Sir. Please ask me to schedule again."

        chosen_start, chosen_end, chosen_label = selected_slot
        selected_window_events = get_upcoming_events(
            50,
            timeMin=chosen_start.astimezone(timezone.utc).isoformat(),
            timeMax=chosen_end.astimezone(timezone.utc).isoformat(),
        )
        log_step("CALENDAR_EVENTS_FETCHED")

        still_conflict = check_conflict(chosen_start, chosen_end, selected_window_events)
        log_step("CONFLICT_CHECK_COMPLETED")

        if still_conflict:
            log_step("TIME_CONFLICT_DETECTED")
            refreshed_slots = suggest_conflict_free_slots(chosen_start + timedelta(minutes=15), duration, count=3)
            log_step("FREE_SLOTS_GENERATED")
            if refreshed_slots:
                PENDING_CONFLICT["slots"] = refreshed_slots
                return (
                    f"Sir, that time is no longer available for '{title}'. Here are updated slots:\n\n"
                    f"{_format_slot_options(refreshed_slots)}\n"
                    "Would you like me to schedule it at one of these times?"
                )

            _reset_state()
            return "Sir, today appears fully booked. Please tell me a different time or day."

        success = create_event(title, chosen_start.isoformat(), chosen_end.isoformat())
        if success:
            log_step("EVENT_CREATED")
            _reset_state()
            _end_scheduling_flow(resolved_user_id)
            return f"Done, Sir. I've scheduled '{title}' for {chosen_label} at {chosen_start.strftime('%I:%M %p')}."

        return "The calendar uplink failed while creating the event, Sir."

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
    force_schedule_intent = False
    if state.last_intent == "AWAITING_TIME":
        extracted_times = _extract_time_tokens(text)
        if extracted_times:
            saved_title = PENDING_DATA.get('title', 'Meeting')
            day_hint = PENDING_DATA.get('day', 'today')
            text = f"schedule {saved_title} " + text
            if day_hint == 'tomorrow':
                text += " tomorrow"
            force_schedule_intent = True
        else:
            saved_title = PENDING_DATA.get('title', 'Meeting')
            day_hint = PENDING_DATA.get('day', 'today')
            return (
                f"I am still waiting for the time to schedule '{saved_title}' for {day_hint}, Sir. "
                "Please say a time like 5 PM or 17:30."
            )

    intent = classify_intent(text)
    intent_source = "rule"
    if intent == "uncertain":
        fallback_intent = await classify_intent_with_llm(text)
        intent = "query_event" if fallback_intent == "general" and any(v in text for v in QUERY_VERBS) and any(k in text for k in QUERY_EVENT_KEYWORDS) else fallback_intent
        intent_source = "llm"
    if force_schedule_intent:
        intent = "schedule"
        intent_source = "context"
    if forced_intent:
        intent = forced_intent
        intent_source = "flow"
    logger.debug(f"[INTENT] source={intent_source} final_intent={intent} text={text}")

    # ══════════════════════════════════════════
    #  STEP 4 — STANDALONE RESCHEDULE INTENT
    # ══════════════════════════════════════════
    if intent == "reschedule":
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
    if intent == "query_event":

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
    if intent == "schedule":
        try:
            _start_scheduling_flow(resolved_user_id)
            target_date = now.date()
            day_context = "today"
            label = f"today, {target_date.strftime('%d %B')}"

            if "tomorrow" in text:
                target_date = (now + timedelta(days=1)).date()
                label = f"tomorrow, {target_date.strftime('%d %B')}"
                day_context = "tomorrow"

            times = _extract_time_tokens(text)

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
                _update_scheduling_flow_data(resolved_user_id, {"title": extracted_title, "day": day_context})
                return f"At what time should I schedule your '{extracted_title}' for {day_context}, Sir?"

            def convert(t):
                hour     = int(t[0])
                minute   = int(t[1]) if t[1] else 0
                meridiem = t[2].replace('.', '').lower()
                if not meridiem:
                    if hour > 12:
                        return hour, minute
                    # Avoid wrong scheduling when user says only "5" or "5:30".
                    raise ValueError("Ambiguous time without AM/PM")
                if 'p' in meridiem and hour < 12: hour += 12
                if 'a' in meridiem and hour == 12: hour = 0
                return hour, minute

            try:
                sh, sm = convert(times[0])
            except ValueError:
                return "Please specify AM or PM for the time, Sir."
            start_dt = datetime.combine(
                target_date,
                datetime.min.time().replace(hour=sh, minute=sm)
            ).replace(tzinfo=IST)

            if "to" in text and len(times) >= 2:
                try:
                    eh, em = convert(times[1])
                except ValueError:
                    return "Please specify AM or PM for the end time as well, Sir."
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

            window_events = get_upcoming_events(
                50,
                timeMin=start_dt.astimezone(timezone.utc).isoformat(),
                timeMax=end_dt.astimezone(timezone.utc).isoformat(),
            )
            log_step("CALENDAR_EVENTS_FETCHED")

            has_conflict = check_conflict(start_dt, end_dt, window_events)
            log_step("CONFLICT_CHECK_COMPLETED")

            if has_conflict:
                log_step("TIME_CONFLICT_DETECTED")
                suggested_slots = suggest_conflict_free_slots(start_dt, end_dt - start_dt, count=3)
                log_step("FREE_SLOTS_GENERATED")

                if suggested_slots:
                    state.last_intent = "AWAITING_CONFLICT_RESOLUTION"
                    PENDING_CONFLICT = {
                        "title": extracted_title,
                        "duration_minutes": int((end_dt - start_dt).total_seconds() // 60),
                        "slots": suggested_slots,
                    }
                    _update_scheduling_flow_data(
                        resolved_user_id,
                        {
                            "title": extracted_title,
                            "day": day_context,
                            "slots": suggested_slots,
                        },
                    )
                    requested_time = start_dt.strftime('%I:%M %p')
                    return (
                        f"Sir, you already have an event at {requested_time}. Here are some available slots:\n\n"
                        f"{_format_slot_options(suggested_slots)}\n"
                        "Would you like me to schedule it at one of these times?"
                    )

                state.last_intent = "AWAITING_TIME"
                PENDING_DATA = {"title": extracted_title, "day": day_context}
                return "Sir, that day appears fully booked. Please say another time or day."

            success = create_event(extracted_title, start_dt.isoformat(), end_dt.isoformat())

            if success:
                log_step("EVENT_CREATED")
                state.last_intent = None
                PENDING_DATA = {}
                _end_scheduling_flow(resolved_user_id)
                return f"Done, Sir. I've scheduled '{extracted_title}' for {label} at {start_dt.strftime('%I:%M %p')}."

            # Keep scheduling mode active until creation succeeds.
            state.last_intent = "AWAITING_TIME"
            PENDING_DATA = {"title": extracted_title, "day": day_context}
            _update_scheduling_flow_data(resolved_user_id, {"title": extracted_title, "day": day_context})
            return "The calendar uplink failed, Sir."

        except Exception as e:
            log_error(f"Scheduling error: {e}")
            # Do not exit scheduling mode on parser/runtime issues.
            fallback_title = PENDING_DATA.get("title", "Meeting")
            fallback_day = PENDING_DATA.get("day", "today")
            state.last_intent = "AWAITING_TIME"
            PENDING_DATA = {"title": fallback_title, "day": fallback_day}
            _update_scheduling_flow_data(resolved_user_id, {"title": fallback_title, "day": fallback_day})
            return (
                f"I am still in scheduling mode for '{fallback_title}' ({fallback_day}), Sir. "
                "Please provide the time again like 5 PM."
            )

    if intent == "rename":
        rename_flow_data = dict(_get_user_flow_state(resolved_user_id).get("flow_data") or {})
        events = get_upcoming_events(15)
        if not events:
            return "I do not see any upcoming events to rename, Sir."

        rename_match = re.search(
            r'(?:rename|retitle|change\s+(?:the\s+)?(?:name|title)\s+(?:of\s+)?)\s+(.+?)\s+(?:to|as)\s+(.+)$',
            text,
            flags=re.IGNORECASE,
        )
        call_it_match = re.search(r'call\s+(?:it|the\s+event|the\s+meeting)\s+(.+)$', text, flags=re.IGNORECASE)

        old_title_hint = None
        new_title = None
        if rename_match:
            old_title_hint = rename_match.group(1).strip(" .?")
            new_title = rename_match.group(2).strip(" .?")
        elif call_it_match:
            new_title = call_it_match.group(1).strip(" .?")

        if not old_title_hint:
            old_title_hint = str(rename_flow_data.get("old_title_hint") or "").strip() or None
        if not new_title:
            flow_title = str(rename_flow_data.get("new_title") or "").strip()
            new_title = flow_title or None

        if not new_title:
            _start_rename_flow(resolved_user_id, rename_flow_data)
            return "Please tell me the new event title, Sir. For example: rename meeting to Project Review."

        target_event = None
        time_tokens = _extract_time_tokens(text)
        if not time_tokens and rename_flow_data.get("target_hour") is not None:
            time_tokens = [
                (
                    str(int(rename_flow_data.get("target_hour") or 0)),
                    str(int(rename_flow_data.get("target_minute") or 0)).zfill(2),
                    str(rename_flow_data.get("target_meridiem") or ""),
                )
            ]
        if time_tokens:
            try:
                target_hour, target_minute = _convert_time_token(time_tokens[0], require_meridiem=False)
            except ValueError:
                return "Please specify AM or PM for the event time, Sir."

            if "tomorrow" in text:
                target_date = now.date() + timedelta(days=1)
                target_day_hint = "tomorrow"
            elif "today" in text:
                target_date = now.date()
                target_day_hint = "today"
            else:
                target_day_hint = str(rename_flow_data.get("target_day") or "today")
                target_date = now.date() + timedelta(days=1) if target_day_hint == "tomorrow" else now.date()

            desired_start = datetime.combine(
                target_date,
                datetime.min.time().replace(hour=target_hour, minute=target_minute),
            ).replace(tzinfo=IST)

            best_candidate = None
            best_delta_minutes = 10**9

            for event in events:
                start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
                if not start_raw:
                    continue

                try:
                    event_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(IST)
                except Exception:
                    try:
                        event_start = datetime.strptime(start_raw, "%Y-%m-%d").replace(tzinfo=IST)
                    except Exception:
                        continue

                if event_start.date() != target_date:
                    continue

                delta_minutes = abs(int((event_start - desired_start).total_seconds() // 60))
                if delta_minutes < best_delta_minutes:
                    best_delta_minutes = delta_minutes
                    best_candidate = event

            # 45-minute window covers small STT rounding issues while still being specific.
            if best_candidate is not None and best_delta_minutes <= 45:
                target_event = best_candidate
            else:
                requested_time = desired_start.strftime('%I:%M %p')
                requested_day = "tomorrow" if target_date != now.date() else "today"
                _start_rename_flow(
                    resolved_user_id,
                    {
                        "new_title": new_title,
                        "old_title_hint": old_title_hint or "",
                        "target_hour": target_hour,
                        "target_minute": target_minute,
                        "target_meridiem": time_tokens[0][2] if len(time_tokens[0]) >= 3 else "",
                        "target_day": target_day_hint,
                    },
                )
                return f"I could not find an event around {requested_time} {requested_day} to rename, Sir."

        generic_hints = {"meeting", "event", "it", "this", "my meeting", "the meeting", "the event"}
        if target_event is None and old_title_hint and old_title_hint not in generic_hints:
            for event in events:
                summary = str(event.get("summary") or "").lower()
                if not summary:
                    continue
                if old_title_hint in summary or summary in old_title_hint:
                    target_event = event
                    break

        if target_event is None:
            target_event = events[0]

        current_title = str(target_event.get("summary") or "Meeting")
        if current_title.lower() == new_title.lower():
            _end_scheduling_flow(resolved_user_id)
            return f"Sir, that event is already named '{current_title}'."

        success = rename_event_title(target_event, new_title)
        if success:
            log_step("EVENT_RENAMED")
            _end_scheduling_flow(resolved_user_id)
            return f"Done, Sir. I renamed '{current_title}' to '{new_title}'."

        _start_rename_flow(resolved_user_id, {"new_title": new_title, "old_title_hint": old_title_hint or ""})
        return "I could not rename the event right now, Sir."

    if intent == "cancel":
        events = get_upcoming_events(10)
        if not events:
            return "There are no upcoming events to cancel, Sir."

        target = None
        for event in events:
            summary = str(event.get("summary") or "").lower()
            if summary and summary in text:
                target = event
                break
        if target is None:
            target = events[0]

        title = target.get("summary", "the event")
        return (
            f"Cancel intent detected for '{title}', Sir. "
            "Direct cancellation is currently disabled in this build; say 'reschedule it' for immediate handling."
        )

    # ══════════════════════════════════════════
    #  STEP 7 — QUICK RESPONSES
    # ══════════════════════════════════════════
    if re.search(TIME_QUERY_REGEX, text):
        return f"The current time is {datetime.now(IST).strftime('%I:%M %p')}."

    if "how are you" in text:
        return "All systems are nominal. Logic processors are primed."

    # ══════════════════════════════════════════
    #  STEP 8 — LLM FALLBACK
    #  Only reaches here if no intent is active
    # ══════════════════════════════════════════
    if state.last_intent != "CONFIRMING_ALERT":
        state.last_intent = None

    memory_task = asyncio.create_task(search_similar(user_text, resolved_user_id, limit=8))
    history_task = asyncio.create_task(get_recent_chat_history(resolved_user_id, limit=MAX_HISTORY))
    profile_task = asyncio.create_task(get_user_profile(resolved_user_id))
    behavior_task = asyncio.create_task(build_behavior_context(resolved_user_id, limit=3))
    retrieved_memories, recent_history, profile, behavior_context = await asyncio.gather(
        memory_task,
        history_task,
        profile_task,
        behavior_task,
    )

    log_step("USER_FETCH_COMPLETED")
    log_step("VECTOR_SEARCH_COMPLETED")

    decayed_memories = apply_memory_decay(retrieved_memories)
    memory_candidates, preference_memories = filter_memories_by_type(decayed_memories)
    selected_memories = smart_memory_selector(memory_candidates, max_memory=MAX_MEMORY)
    compressed_points = compress_context(selected_memories, recent_history)

    if preference_memories:
        pref_points = []
        for pref in preference_memories[:2]:
            pref_text = (pref.get("user_text") or "").strip()
            if pref_text:
                pref_points.append(f"* {pref_text[:120]}")
        preference_context = "\n".join(pref_points)
    else:
        preference_context = ""

    memory_context = "\n".join(f"* {point}" for point in compressed_points)
    profile_context = format_profile_section(profile)
    if preference_context:
        profile_context = f"{profile_context}\n\n## Preference memories:\n{preference_context}".strip()

    log_step("CONTEXT_BUILD_COMPLETED")

    response = await get_aries_response(
        user_text,
        resolved_user_id,
        memory_context=memory_context,
        profile_context=profile_context,
        behavior_context=behavior_context,
        recent_history=recent_history[-MAX_HISTORY:],
    )

    if response.strip().upper().startswith("SEARCH"):
        try:
            query    = response.split("SEARCH", 1)[-1].strip(" []:")
            web_data = fetch_live_info(query)
            refined  = (
                f"Found info: {web_data}\n"
                f"User asked: {user_text}\n"
                "Answer factually first, then add a short witty remark if appropriate."
            )
            return await get_aries_response(
                refined,
                resolved_user_id,
                memory_context=memory_context,
                profile_context=profile_context,
                behavior_context=behavior_context,
            )
        except Exception:
            return "Web search failed. Relying on local core."

    return response


# ─────────────────────────────────────────────
#  CALLED BY main.py when alert fires
# ─────────────────────────────────────────────
def handle_alert_event(event: dict) -> str:
    """Sets shared state and returns the spoken alert message."""
    if str(event.get("event_type") or "").lower() == "behavior_suggestion":
        return str(event.get("message") or "Sir, I have a behavior suggestion for you.")

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