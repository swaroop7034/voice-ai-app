from ddgs import DDGS

def fetch_live_info(query):
    """
    Searches the web and returns a summarized text block for Aris.
    """
    try:
        with DDGS() as ddgs:
            # max_results=3 keeps it fast for voice interaction
            results = list(ddgs.text(query, max_results=3))
            
            if not results:
                return "No real-time data found for that query."

            # Clean and format the data for the LLM
            context = "\n".join([f"Source: {r['title']}\nSnippet: {r['body']}" for r in results])
            return context
    except Exception as e:
        return f"Search error: {str(e)}"

# Quick test (Run this file directly to verify)
if __name__ == "__main__":
    print(fetch_live_info("Current weather in Kochi"))