import time
import asyncio
from datetime import datetime, timedelta, timezone
from tools.calendar_module import get_upcoming_events
from core.state import alert_queue
from core.behavior_analyzer import (
    analyze_patterns,
    store_patterns,
    generate_suggestions,
    fetch_unseen_suggestions,
    mark_suggestions_seen,
)
from integrations.supabase_store import get_default_user_id

IST = timezone(timedelta(hours=5, minutes=30))


def _run_behavior_suggestion_cycle(now: datetime) -> None:
    """Generate one behavior-based proactive suggestion and enqueue it for voice delivery."""
    user_id = get_default_user_id()

    async def _generate_once() -> None:
        patterns = await analyze_patterns(user_id)
        await store_patterns(user_id, patterns)
        await generate_suggestions(user_id)

        unseen = await fetch_unseen_suggestions(user_id, limit=1)
        if not unseen:
            print(f"[PROACTIVE] No behavior suggestion to send at {now.strftime('%H:%M')}")
            return

        item = unseen[0]
        suggestion_text = str(item.get("suggestion_text") or "").strip()
        suggestion_id = item.get("id")

        if not suggestion_text:
            return

        if suggestion_id is not None:
            await mark_suggestions_seen([int(suggestion_id)])

        alert_queue.append(
            {
                "event_type": "behavior_suggestion",
                "summary": "Behavior Suggestion",
                "start": {"dateTime": now.isoformat()},
                "message": f"Sir, based on your recent behavior: {suggestion_text}",
                "user_id": user_id,
            }
        )
        print(f"[PROACTIVE] Queued behavior suggestion for {user_id} at {now.strftime('%H:%M')}")

    try:
        asyncio.run(_generate_once())
    except Exception as exc:
        print(f"[PROACTIVE ERROR] Behavior suggestion cycle failed: {exc}")


def monitor_schedule():
    print("[PROACTIVE] Aries is monitoring your schedule, Sir.")

    alert_history = {}
    daily_suggestion_history: set[str] = set()

    while True:
        try:
            events = get_upcoming_events(10)
            now    = datetime.now(IST)

            # Fire daily behavior suggestions at 10:00 and 19:00 IST.
            daily_slots = [(10, 0), (19, 0)]
            for hour, minute in daily_slots:
                if now.hour == hour and now.minute == minute:
                    slot_key = f"{now.date().isoformat()}-{hour:02d}:{minute:02d}"
                    if slot_key not in daily_suggestion_history:
                        _run_behavior_suggestion_cycle(now)
                        daily_suggestion_history.add(slot_key)

            if events:
                for event in events:
                    event_id   = event.get('id')
                    event_name = event.get('summary', 'Unnamed Event')

                    start_str = event['start'].get('dateTime') or event['start'].get('date')

                    if not start_str or not event_id:
                        continue

                    # Skip all-day events
                    if 'T' not in start_str:
                        continue

                    start_time = datetime.fromisoformat(
                        start_str.replace('Z', '+00:00')
                    ).astimezone(IST)

                    diff = start_time - now

                    # Alert window: 0 – 30 minutes before
                    if timedelta(minutes=0) <= diff <= timedelta(minutes=30):

                        # Re-alert if the meeting was moved (updated timestamp changes)
                        updated_at      = event.get('updated', '')
                        event_state_key = f"{event_id}_{updated_at}"

                        if event_state_key not in alert_history:
                            mins = int(diff.total_seconds() // 60)

                            if mins <= 0:
                                time_msg = "is starting now"
                            elif mins == 1:
                                time_msg = "starts in 1 minute"
                            else:
                                time_msg = f"starts in {mins} minutes"

                            print(f"\n[PROACTIVE ALERT]: {event_name} {time_msg}")

                            # Push the whole event — main.py passes it to brain.handle_alert_event()
                            alert_queue.append(event)

                            alert_history[event_state_key] = now

            # Prune history older than 2 hours
            cutoff       = now - timedelta(hours=2)
            alert_history = {k: v for k, v in alert_history.items() if v > cutoff}
            # Keep history small while preserving today's and recent slots.
            daily_suggestion_history = {
                key for key in daily_suggestion_history
                if key >= (now - timedelta(days=2)).date().isoformat()
            }

            time.sleep(60)

        except Exception as e:
            print(f"[PROACTIVE ERROR] {e}")
            time.sleep(30)