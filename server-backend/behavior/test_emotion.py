from emotion import EmotionAnalyzer

ea = EmotionAnalyzer()

text = "I am very happy about exams"
emotion = ea.get_emotion(text)

print("Input:", text)
print("Emotion:", emotion)
