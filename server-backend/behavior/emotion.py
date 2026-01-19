from transformers import pipeline

class EmotionAnalyzer:
    def __init__(self):
        self.pipe = pipeline("text-classification", model="j-hartmann/emotion-english-distilroberta-base", top_k=1)

    def get_emotion(self, text: str) -> str:
        out = self.pipe(text)[0][0]
        return out["label"].lower()
