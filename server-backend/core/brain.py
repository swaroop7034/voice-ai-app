from datetime import datetime
from core.llm_manager import get_aries_response

def process_text(user_text: str) -> str:
    """
    Decides whether to use a local hardcoded command or 
    the local Llama model.
    """
    text = user_text.lower().strip()

    if not text:
        return "I am standing by, sir."

    # --- LOCAL FAST COMMANDS ---
    if "time" in text:
        return f"The current time is {datetime.now().strftime('%I:%M %p')}."

    if "how are you" in text:
        return "All systems are nominal. My core temperature is stable."

    # --- LOCAL AI FALLBACK ---
    return get_aries_response(user_text)