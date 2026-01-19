from sentiment import SentimentAnalyzer

sa = SentimentAnalyzer()

text = "I am tired of studying for exams"
sentiment = sa.get_sentiment(text)

print("Input:", text)
print("Sentiment:", sentiment)
