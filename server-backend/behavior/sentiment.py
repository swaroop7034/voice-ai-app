from transformers import pipeline

class SentimentAnalyzer:
    def __init__(self):
        self.pipe = pipeline("sentiment-analysis")

    def get_sentiment(self, text: str) -> str:
        result = self.pipe(text)[0]
        label = result["label"].lower()
        if label in ["positive", "negative", "neutral"]:
            return label
        return "neutral"
