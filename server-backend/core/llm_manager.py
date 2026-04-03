import ollama
from datetime import datetime  # Added this import

# This list stays in RAM while the server is running.
chat_history = []

def get_aries_response(user_text: str) -> str:
    global chat_history
    """
    Communicates with Ollama using a sliding window memory 
    and a search-triggering system prompt.
    """
    try:
        print(f"[LOCAL AI] Inference started for: {user_text}")

        # Get current date for temporal grounding
        today_date = datetime.now().strftime("%B %d, %Y")

        # 1. Add current user input to memory
        chat_history.append({'role': 'user', 'content': user_text})

        # 2. Memory Management: Keep only the last 10 messages
        if len(chat_history) > 10:
            chat_history = chat_history[-10:]

        # 3. Updated System Instructions with Dynamic Date
        system_prompt = {
            'role': 'system',
            'content': (
                "You are Aries, a witty and advanced AI assistant inspired by JARVIS. "
                "TECHNICAL CONTEXT: You are running LOCALLY on the user's RTX 3050 GPU. "
                "PERSONALITY: Be slightly formal but witty. Refer to the user as 'Sir'. "
                f"CURRENT DATE: {today_date}. "  # 1. Dynamic date injected here
                "IMPORTANT: You are currently in the year 2026. "
                "SEARCH PROTOCOL: If the user asks about events in 2025 or 2026, current news, weather, live data, "
                "or details you do not have in your local offline training, you MUST respond strictly "
                "with SEARCH[query]. Do not guess. Do not be poetic. Just SEARCH."
                "CONSTRAINTS: Keep final responses to 1 or 2 sentences max."
            )
        }

        # 4. Construct the full message list
        messages_to_send = [system_prompt] + chat_history

        response = ollama.chat(
            model='llama3.2',
            messages=messages_to_send,
        )
        
        ai_message = response['message']['content']

        # 5. Add Aries' response to history
        chat_history.append({'role': 'assistant', 'content': ai_message})

        print(f"[LOCAL AI] Response generated.")
        return ai_message
        
    except Exception as e:
        print(f"[LOCAL AI ERROR] Connection to Ollama failed: {e}")
        return "My memory banks are currently inaccessible, Sir. Please check the local engine."