from datetime import datetime
from core.sendtollm import send_to_llm

def process_text(user_text: str) -> str:
    """
    Decides assistant response.
    Fast command handling + LLM fallback.
    """

    text = user_text.lower().strip()

    # Fast exits
    if not text:
        return "I didn't catch that."

    if "exit" in text or "quit" in text:
        return "Goodbye. Have a nice day."

    if "time" in text:
        return f"The time is {datetime.now().strftime('%I:%M %p')}"

    if "how are you" in text:
        return "I am doing well. How can I help you?"

    # Fallback to LLM (mock or real)
    return send_to_llm(text)
