import re

class TopicClassifier:
    TOPIC_MAP = {
        "study": ["exam", "study", "homework", "class", "college", "test"],
        "health": ["doctor", "medicine", "health", "tired", "sick"],
        "work": ["office", "meeting", "project", "deadline"],
        "personal": ["family", "friend", "relationship"],
        "routine": ["alarm", "wake", "sleep", "breakfast"],
    }

    def get_topic(self, text: str) -> str:
        text = text.lower()
        for topic, keywords in self.TOPIC_MAP.items():
            for k in keywords:
                if k in text:
                    return topic
        return "misc"
