# core/state.py

# 1. Stores the upcoming events found by the scheduler
alert_queue = [] 

# 2. Stores the specific event dictionary Aries is currently asking you about
current_alert_event = None

# 3. Remembers if Aries is waiting for your "Yes/No" to an alert
# Values: None, "CONFIRMING_ALERT", "AWAITING_TIME"
last_intent = None