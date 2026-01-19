from datetime import datetime
from .sentiment import SentimentAnalyzer
from .emotion import EmotionAnalyzer
from .topic import TopicClassifier
from .time_context import TimeContext
from .behaviour_event import BehaviorEvent

class BehaviorAnalytics:
    def __init__(self):
        self.sentiment_analyzer = SentimentAnalyzer()
        self.emotion_analyzer = EmotionAnalyzer()
        self.topic_classifier = TopicClassifier()
        self.time_context = TimeContext()

    def analyze(self, text: str, timestamp: datetime = None):
        if timestamp is None:
            timestamp = datetime.now()

        sentiment = self.sentiment_analyzer.get_sentiment(text)
        emotion = self.emotion_analyzer.get_emotion(text)
        topic = self.topic_classifier.get_topic(text)
        time_of_day = self.time_context.get_time_of_day(timestamp)

        event = BehaviorEvent(
            text=text,
            sentiment=sentiment,
            emotion=emotion,
            topic=topic,
            time_of_day=time_of_day,
            timestamp=timestamp
        )

        return event
