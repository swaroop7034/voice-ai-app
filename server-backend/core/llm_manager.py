import ollama

# This list stays in RAM while the server is running.
# It stores the 'user' and 'assistant' roles to maintain context.
chat_history = []

def get_aries_response(user_text: str) -> str:
    global chat_history
    """
    Communicates with Ollama using a sliding window memory 
    to provide conversational context.
    """
    try:
        print(f"[LOCAL AI] Inference started for: {user_text}")

        # 1. Add current user input to memory
        chat_history.append({'role': 'user', 'content': user_text})

        # 2. Memory Management: Keep only the last 6 messages (3 exchanges)
        # This prevents the context from growing too large.
        if len(chat_history) > 10:
            chat_history = chat_history[-10:]

        # 3. Define the System Instructions
        system_prompt = {
            'role': 'system',
            'content': (
                "You are Aries, a witty and advanced AI assistant inspired by JARVIS. "
                "TECHNICAL CONTEXT: You are running LOCALLY on the user's RTX 3050 GPU. "
                "PERSONALITY: Be slightly formal but witty. Refer to the user as 'Sir'. "
                "CONSTRAINTS: Keep responses to 1 or 2 sentences max."
            )
        }

        # 4. Construct the full message list: System Prompt + History
        messages_to_send = [system_prompt] + chat_history

        response = ollama.chat(
            model='llama3.2',
            messages=messages_to_send,
        )
        
        ai_message = response['message']['content']

        # 5. Add Aries' response to history so he remembers what he said
        chat_history.append({'role': 'assistant', 'content': ai_message})

        print(f"[LOCAL AI] Response generated with context memory.")
        return ai_message
        
    except Exception as e:
        print(f"[LOCAL AI ERROR] Connection to Ollama failed: {e}")
        return "My memory banks are currently inaccessible, Sir. Please check the local engine."