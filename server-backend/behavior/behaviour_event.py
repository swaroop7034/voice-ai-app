from dataclasses import dataclass
from datetime import datetime

@dataclass
class BehaviorEvent:
    text: str
    sentiment: str
    emotion: str
    topic: str
    time_of_day: str
    timestamp: datetime
