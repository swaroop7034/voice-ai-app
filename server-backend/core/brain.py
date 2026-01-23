import os
from dotenv import load_dotenv
from groq import Groq

# load .env for GROQ_API_KEY
load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY:
    raise Exception("Missing GROQ_API_KEY in .env file")

client = Groq(api_key=API_KEY)

MODEL = "llama-3.1-8b-instant"   # works with 1.0.0

def get_ai_response(user_text: str) -> str:
    
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "user", "content": user_text}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Error from AI: {e}"


# test mode only
if __name__ == "__main__":
    print(get_ai_response("St. Josephs college of enginering pala in one linecls"))
