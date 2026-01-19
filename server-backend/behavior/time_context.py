from datetime import datetime

class TimeContext:
    def get_time_of_day(self, timestamp: datetime) -> str:
        hour = timestamp.hour

        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"
