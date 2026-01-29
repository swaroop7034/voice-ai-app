def send_to_llm(user_text):
    """(Mock implementation for testing)

    Sends user text to LLM and returns response text"""


    print("[LLM] Received text:", user_text)

    # Simulated LLM response
    response_text = f"I understood you said: {user_text}"

    print("[LLM] Generated response:", response_text)
    return response_text


# Standalone test
if __name__ == "__main__":
    test_input = "Hello ARIS"
    reply = send_to_llm(test_input)
    print("[LLM] Final output:", reply)
