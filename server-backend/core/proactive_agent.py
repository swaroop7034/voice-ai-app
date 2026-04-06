import time
from datetime import datetime, timedelta, timezone
from tools.calendar_module import get_upcoming_events
from core.state import alert_queue
from logger import log_error, log_step

IST = timezone(timedelta(hours=5, minutes=30))


def monitor_schedule():
    log_step("PROACTIVE_MONITOR_STARTED")

    alert_history = {}

    while True:
        try:
            events = get_upcoming_events(10)
            now    = datetime.now(IST)

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

                            log_step("PROACTIVE_ALERT_QUEUED")

                            # Push the whole event — main.py passes it to brain.handle_alert_event()
                            alert_queue.append(event)

                            alert_history[event_state_key] = now

            # Prune history older than 2 hours
            cutoff       = now - timedelta(hours=2)
            alert_history = {k: v for k, v in alert_history.items() if v > cutoff}

            time.sleep(60)

        except Exception as e:
            log_error(f"PROACTIVE error: {e}")
            time.sleep(30)