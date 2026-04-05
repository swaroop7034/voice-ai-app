import os.path
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from logger import log_debug, log_error, log_step

SCOPES = ['https://www.googleapis.com/auth/calendar']
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Working hours: 8 AM to 9 PM IST
WORK_START_HOUR = 8
WORK_END_HOUR   = 21

# How many days ahead to search for a free slot
MAX_SEARCH_DAYS = 5


# ─────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────
def get_calendar_service():
    creds = None
    if not os.path.exists('data'):
        os.makedirs('data')

    token_path = 'data/token.json'
    creds_path = 'data/credentials.json'

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log_error(f"AUTH token refresh failed ({e}), forcing re-auth.")
                creds = None  # fall through to full re-auth below

        if not creds or not creds.valid:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(f"{creds_path} not found.")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


# ─────────────────────────────────────────────
#  FETCH EVENTS
# ─────────────────────────────────────────────
def get_upcoming_events(max_results=10, timeMin=None, timeMax=None):
    try:
        service = get_calendar_service()

        if not timeMin:
            timeMin = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        params = dict(
            calendarId='primary',
            timeMin=timeMin,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        )
        if timeMax:
            params['timeMax'] = timeMax

        events = service.events().list(**params).execute()
        items = events.get('items', [])

        # Best-effort sync of Google-detected events into Supabase.
        try:
            from integrations.supabase_store import get_default_user_id, sync_calendar_events_sync

            sync_calendar_events_sync(
                user_id=get_default_user_id(),
                events=items,
                source="google_calendar_detected",
            )
        except Exception as sync_exc:
            log_error(f"CALENDAR sync detect->supabase failed: {sync_exc}")

        return items

    except Exception as e:
        log_error(f"Calendar fetching error: {e}")
        return []


def get_events_for_date(target_date: datetime.date):
    """Return all events on a specific date (IST), using UTC boundaries for the API."""
    # Convert IST midnight → UTC for Google Calendar query
    day_start_ist = datetime.datetime(
        target_date.year, target_date.month, target_date.day,
        0, 0, 0, tzinfo=IST
    )
    day_end_ist = day_start_ist.replace(hour=23, minute=59, second=59)

    time_min = day_start_ist.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    time_max = day_end_ist.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    return get_upcoming_events(50, timeMin=time_min, timeMax=time_max)


def get_today_events():
    return get_events_for_date(datetime.datetime.now(IST).date())


def _parse_event_bounds(event: dict) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    start_raw = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
    end_raw = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
    if not start_raw:
        return None, None

    try:
        start_dt = datetime.datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
    except Exception:
        return None, None

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=IST)
    else:
        start_dt = start_dt.astimezone(IST)

    if end_raw:
        try:
            end_dt = datetime.datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=IST)
            else:
                end_dt = end_dt.astimezone(IST)
        except Exception:
            end_dt = start_dt + datetime.timedelta(hours=1)
    else:
        end_dt = start_dt + datetime.timedelta(hours=1)

    if end_dt <= start_dt:
        end_dt = start_dt + datetime.timedelta(hours=1)

    return start_dt, end_dt


def check_conflict(start_time: datetime.datetime, end_time: datetime.datetime, events: list[dict]) -> bool:
    for event in events:
        event_start, event_end = _parse_event_bounds(event)
        if event_start is None or event_end is None:
            continue
        if start_time < event_end and end_time > event_start:
            return True
    return False


def filter_slots_by_duration(
    free_slots: list[tuple[datetime.datetime, datetime.datetime]],
    duration: datetime.timedelta,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    return [(slot_start, slot_end) for slot_start, slot_end in free_slots if (slot_end - slot_start) >= duration]


def find_free_slots(
    events: list[dict],
    day_start: datetime.datetime,
    day_end: datetime.datetime,
    duration: datetime.timedelta,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    free_slots: list[tuple[datetime.datetime, datetime.datetime]] = []
    current = day_start

    intervals: list[tuple[datetime.datetime, datetime.datetime]] = []
    for event in events:
        event_start, event_end = _parse_event_bounds(event)
        if event_start is None or event_end is None:
            continue
        if event_end <= day_start or event_start >= day_end:
            continue
        clipped_start = max(event_start, day_start)
        clipped_end = min(event_end, day_end)
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))

    for event_start, event_end in sorted(intervals, key=lambda x: x[0]):
        if current < event_start:
            free_slots.append((current, event_start))
        current = max(current, event_end)

    if current < day_end:
        free_slots.append((current, day_end))

    return filter_slots_by_duration(free_slots, duration)


def suggest_conflict_free_slots(
    requested_start: datetime.datetime,
    duration: datetime.timedelta,
    count: int = 3,
) -> list[tuple[datetime.datetime, datetime.datetime, str]]:
    suggested: list[tuple[datetime.datetime, datetime.datetime, str]] = []
    requested_start = requested_start.astimezone(IST)
    today = datetime.datetime.now(IST).date()

    for day_offset in range(MAX_SEARCH_DAYS * 2):
        if len(suggested) >= count:
            break

        target_date = requested_start.date() + datetime.timedelta(days=day_offset)
        day_start = datetime.datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            WORK_START_HOUR,
            0,
            0,
            tzinfo=IST,
        )
        day_end = datetime.datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            WORK_END_HOUR,
            0,
            0,
            tzinfo=IST,
        )

        window_start = max(day_start, requested_start) if day_offset == 0 else day_start
        if window_start >= day_end:
            continue

        events = get_events_for_date(target_date)
        free_ranges = find_free_slots(events, window_start, day_end, duration)
        day_label = _day_label(target_date, today)

        for free_start, free_end in free_ranges:
            if len(suggested) >= count:
                break
            slot_start = free_start
            while slot_start + duration <= free_end and len(suggested) < count:
                slot_end = slot_start + duration
                suggested.append((slot_start, slot_end, day_label))
                slot_start = slot_start + duration

    return suggested


def get_primary_calendar_user_id():
    try:
        service = get_calendar_service()
        response = service.calendarList().list(maxResults=20).execute()
        calendars = response.get('items', [])

        for calendar in calendars:
            if calendar.get('primary'):
                return calendar.get('id') or calendar.get('summary')

        if calendars:
            first_calendar = calendars[0]
            return first_calendar.get('id') or first_calendar.get('summary')

    except Exception as e:
        log_debug(f"[CALENDAR USER ID] {e}")

    return None


# ─────────────────────────────────────────────
#  SLOT FINDER  (single day)
# ─────────────────────────────────────────────
def find_free_slot(events, window_start, window_end, duration):
    """
    Find the earliest free slot of `duration` between window_start and window_end.
    Both window bounds and event times must be tz-aware (IST).
    """
    current = window_start
    sorted_events = sorted(
        events,
        key=lambda e: e['start'].get('dateTime', e['start'].get('date', ''))
    )

    for e in sorted_events:
        s_raw = e['start'].get('dateTime') or e['start'].get('date')
        e_raw = e['end'].get('dateTime') or e['end'].get('date')
        if not s_raw or not e_raw:
            continue

        s_dt = datetime.datetime.fromisoformat(s_raw.replace('Z', '+00:00')).astimezone(IST)
        e_dt = datetime.datetime.fromisoformat(e_raw.replace('Z', '+00:00')).astimezone(IST)

        if s_dt >= window_end:
            break  # no point looking further

        if (s_dt - current) >= duration:
            return current  # gap before this event is big enough

        current = max(current, e_dt)

    # Check remaining time after last event
    if (window_end - current) >= duration:
        return current

    return None


# ─────────────────────────────────────────────
#  MULTI-DAY FREE SLOT SEARCH
# ─────────────────────────────────────────────
def find_next_free_slot(duration: datetime.timedelta, search_from: datetime.datetime):
    """
    Search up to MAX_SEARCH_DAYS starting from `search_from` for a free slot
    of the given duration within working hours.

    Returns:
        (slot_datetime, day_label, skipped_days_message)
        slot_datetime  → tz-aware IST datetime of the free slot, or None if not found
        day_label      → human-readable string like "today" / "tomorrow" / "Thursday, 5 April"
        skipped_msg    → e.g. "Sir, no free slot today." or "" if found on first day
    """
    now = datetime.datetime.now(IST)
    today = now.date()
    skipped_messages = []

    for day_offset in range(MAX_SEARCH_DAYS):
        target_date = today + datetime.timedelta(days=day_offset)

        # Build working-hour window for this day
        work_start = datetime.datetime(
            target_date.year, target_date.month, target_date.day,
            WORK_START_HOUR, 0, 0, tzinfo=IST
        )
        work_end = datetime.datetime(
            target_date.year, target_date.month, target_date.day,
            WORK_END_HOUR, 0, 0, tzinfo=IST
        )

        # On the first day, don't search in the past — start from search_from
        window_start = max(work_start, search_from) if day_offset == 0 else work_start

        # If effective window is too short for the duration, skip
        if (work_end - window_start) < duration:
            label = _day_label(target_date, today)
            skipped_messages.append(f"no free slot {label}")
            continue

        events = get_events_for_date(target_date)
        slot = find_free_slot(events, window_start, work_end, duration)

        if slot:
            day_lbl = _day_label(target_date, today)
            skip_msg = (
                f"Sir, there's {_join_skipped(skipped_messages)}. "
                if skipped_messages else ""
            )
            return slot, day_lbl, skip_msg

        label = _day_label(target_date, today)
        skipped_messages.append(f"no free slot {label}")

    return None, None, f"Sir, I couldn't find a free slot in the next {MAX_SEARCH_DAYS} days."


def _day_label(target_date, today):
    if target_date == today:
        return "today"
    if target_date == today + datetime.timedelta(days=1):
        return "tomorrow"
    # %-d is Linux-only; use %d and strip leading zero manually
    return target_date.strftime('%A, %d %B').replace(' 0', ' ')


def _join_skipped(msgs):
    if not msgs:
        return ""
    if len(msgs) == 1:
        return msgs[0]
    return ", ".join(msgs[:-1]) + " and " + msgs[-1]


# ─────────────────────────────────────────────
#  SUGGEST MULTIPLE RESCHEDULE SLOTS
# ─────────────────────────────────────────────
def find_multiple_free_slots(duration: datetime.timedelta, search_from: datetime.datetime, count: int = 5):
    """
    Find up to `count` free slots of `duration` starting from `search_from`.
    Scans each day exhaustively for ALL gaps, in chronological order.
    Returns list of (slot_datetime, day_label) tuples.
    """
    today = datetime.datetime.now(IST).date()
    slots = []

    for day_offset in range(MAX_SEARCH_DAYS * 2):
        if len(slots) >= count:
            break

        target_date  = today + datetime.timedelta(days=day_offset)
        work_start   = datetime.datetime(target_date.year, target_date.month, target_date.day,
                                         WORK_START_HOUR, 0, 0, tzinfo=IST)
        work_end     = datetime.datetime(target_date.year, target_date.month, target_date.day,
                                         WORK_END_HOUR, 0, 0, tzinfo=IST)
        window_start = max(work_start, search_from) if day_offset == 0 else work_start

        if (work_end - window_start) < duration:
            continue

        events = get_events_for_date(target_date)
        day_label = _day_label(target_date, today)

        # Sort events by start time
        sorted_events = sorted(
            [e for e in events if e['start'].get('dateTime')],
            key=lambda e: e['start']['dateTime']
        )

        current = window_start

        for e in sorted_events:
            if len(slots) >= count:
                break

            s_raw = e['start'].get('dateTime')
            e_raw = e['end'].get('dateTime') or e['end'].get('date')
            if not s_raw or not e_raw:
                continue

            s_dt = datetime.datetime.fromisoformat(s_raw.replace('Z', '+00:00')).astimezone(IST)
            e_dt = datetime.datetime.fromisoformat(e_raw.replace('Z', '+00:00')).astimezone(IST)

            # Skip events that end before our search window starts
            if e_dt <= current:
                continue

            # Gap between current pointer and this event's start
            gap_end = min(s_dt, work_end)
            while current + duration <= gap_end and len(slots) < count:
                slots.append((current, day_label))
                current = current + duration  # pack slots back-to-back within gap

            # Move pointer past this event
            current = max(current, e_dt)

        # Remaining time after all events until work_end
        while current + duration <= work_end and len(slots) < count:
            slots.append((current, day_label))
            current = current + duration

    return slots


def suggest_reschedule_slots(event, count: int = 5):
    log_debug(f"[DEBUG EVENT] summary={event.get('summary')} start={event['start']} end={event.get('end')}")
    try:
        start_str = event['start'].get('dateTime') or event['start'].get('date')
        end_str   = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')

        if not start_str:
            return "Sir, the event has no start time — I can't reschedule it.", []

        start_dt = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(IST)

        # If no end time, or end before start, default to 1 hour
        if end_str:
            end_dt = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(IST)
            duration = end_dt - start_dt
            if duration.total_seconds() <= 0:
                log_debug("[WARN] Event has zero/negative duration — defaulting to 1 hour")
                duration = datetime.timedelta(hours=1)
        else:
            log_debug("[WARN] Event has no end time — defaulting to 1 hour")
            duration = datetime.timedelta(hours=1)

        search_from = datetime.datetime.now(IST) + datetime.timedelta(minutes=30)
        raw_slots   = find_multiple_free_slots(duration, search_from, count)

        if not raw_slots:
            return (
                f"Sir, I couldn't find any free slots in the next {MAX_SEARCH_DAYS * 2} days.",
                []
            )

        dur_hrs  = int(duration.total_seconds() // 3600)
        dur_mins = int((duration.total_seconds() % 3600) // 60)
        dur_str  = ""
        if dur_hrs:  dur_str += f"{dur_hrs} hour{'s' if dur_hrs > 1 else ''}"
        if dur_mins: dur_str += f" {dur_mins} minute{'s' if dur_mins > 1 else ''}"

        result_slots = []
        lines = []
        for i, (slot_start, day_label) in enumerate(raw_slots, 1):
            slot_end = slot_start + duration
            time_str = slot_start.strftime('%I:%M %p')
            lines.append(f"Option {i}: {day_label} at {time_str}")
            result_slots.append((slot_start, slot_end, day_label))

        options_text = ", ".join(lines)
        msg = (
            f"I found {len(result_slots)} available {dur_str.strip()} slot{'s' if len(result_slots) > 1 else ''} "
            f"for '{event.get('summary', 'the meeting')}': {options_text}. "
            "Which option would you like, Sir?"
        )
        return msg, result_slots

    except Exception as e:
        log_error(f"suggest_reschedule_slots error: {e}")
        return "I ran into an issue finding free slots, Sir.", []


# Keep old single-slot version for backward compat
def suggest_reschedule_slot(event):
    msg, slots = suggest_reschedule_slots(event, count=1)
    if slots:
        s, e, _ = slots[0]
        return msg, s, e
    return msg, None, None


# ─────────────────────────────────────────────
#  CONFIRM RESCHEDULE  (actually moves the event)
# ─────────────────────────────────────────────
def confirm_reschedule(event, new_start: datetime.datetime, new_end: datetime.datetime):
    """
    Update the Google Calendar event to new_start / new_end.
    Returns a response string.
    """
    try:
        service = get_calendar_service()

        event['start'] = {'dateTime': new_start.isoformat(), 'timeZone': 'Asia/Kolkata'}
        event['end']   = {'dateTime': new_end.isoformat(),   'timeZone': 'Asia/Kolkata'}

        service.events().update(
            calendarId='primary',
            eventId=event['id'],
            body=event
        ).execute()

        day_label = _day_label(new_start.date(), datetime.datetime.now(IST).date())
        time_str  = new_start.strftime('%I:%M %p')

        return (
            f"Done, Sir. '{event.get('summary', 'The meeting')}' has been rescheduled "
            f"to {day_label} at {time_str}."
        )

    except Exception as e:
        log_error(f"confirm_reschedule error: {e}")
        return "The reschedule failed, Sir. Calendar uplink error."


# ─────────────────────────────────────────────
#  LEGACY: handle_auto_reschedule
#  Kept for backward compat — now delegates to the two-step flow
# ─────────────────────────────────────────────
def handle_auto_reschedule(event):
    """
    Deprecated single-call reschedule.
    Now just finds + immediately applies — use suggest/confirm in brain.py instead.
    """
    msg, new_start, new_end = suggest_reschedule_slot(event)
    if new_start is None:
        return msg
    return confirm_reschedule(event, new_start, new_end)


# ─────────────────────────────────────────────
#  CREATE EVENT
# ─────────────────────────────────────────────
def create_event(summary, start_iso, end_iso):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': 'Scheduled via Aries AI',
            'start': {'dateTime': start_iso, 'timeZone': 'Asia/Kolkata'},
            'end':   {'dateTime': end_iso,   'timeZone': 'Asia/Kolkata'},
        }
        created_event = service.events().insert(calendarId='primary', body=event).execute()

        # Best-effort sync of ARIS-created event into Supabase.
        try:
            from integrations.supabase_store import get_default_user_id, upsert_calendar_event_sync

            upsert_calendar_event_sync(
                user_id=get_default_user_id(),
                event=created_event,
                source="aris_created",
            )
        except Exception as sync_exc:
            log_error(f"CALENDAR sync aris_created->supabase failed: {sync_exc}")

        return True
    except Exception as e:
        log_error(f"Calendar create error: {e}")
        return False


def rename_event_title(event: dict, new_summary: str) -> bool:
    """Rename a Google Calendar event summary/title."""
    try:
        service = get_calendar_service()
        event['summary'] = new_summary
        updated_event = service.events().update(
            calendarId='primary',
            eventId=event['id'],
            body=event,
        ).execute()

        try:
            from integrations.supabase_store import get_default_user_id, upsert_calendar_event_sync

            upsert_calendar_event_sync(
                user_id=get_default_user_id(),
                event=updated_event,
                source="aris_renamed",
            )
        except Exception as sync_exc:
            log_error(f"CALENDAR sync aris_renamed->supabase failed: {sync_exc}")

        return True
    except Exception as e:
        log_error(f"Calendar rename error: {e}")
        return False


# ─────────────────────────────────────────────
#  MANUAL RESCHEDULE  (user gives explicit time)
# ─────────────────────────────────────────────
def reschedule_next_event(new_time_str):
    """Reschedule the very next upcoming event to new_time_str (HH:MM), preserving original duration."""
    try:
        service = get_calendar_service()
        events = get_upcoming_events(1)

        if not events:
            return "No upcoming events, Sir."

        event = events[0]

        start_str = event['start'].get('dateTime') or event['start'].get('date')
        end_str   = event['end'].get('dateTime')   or event['end'].get('date')

        start_dt = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(IST)
        end_dt   = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(IST)
        duration = end_dt - start_dt  # ← preserve original duration (was hardcoded to 1hr before)

        hour, minute = map(int, new_time_str.split(':'))
        new_start = datetime.datetime.now(IST).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        new_end = new_start + duration

        event['start'] = {'dateTime': new_start.isoformat(), 'timeZone': 'Asia/Kolkata'}
        event['end']   = {'dateTime': new_end.isoformat(),   'timeZone': 'Asia/Kolkata'}

        service.events().update(
            calendarId='primary', eventId=event['id'], body=event
        ).execute()

        return f"Moved '{event.get('summary', 'the event')}' to {new_time_str}, Sir."

    except Exception as e:
        log_error(f"Manual reschedule error: {e}")
        return "Error updating event, Sir."