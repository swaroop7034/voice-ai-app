from ddgs import DDGS

def fetch_live_info(query):
    """
    Searches the web and returns a concise source+snippet context block.
    """
    try:
        with DDGS() as ddgs:
            # max_results=3 keeps it fast for voice interaction
            results = list(ddgs.text(query, max_results=3))

            if not results:
                return ""

            lines = []
            for item in results:
                title = str(item.get("title") or "Untitled source").strip()
                snippet = str(item.get("body") or "").strip()
                url = str(item.get("href") or "").strip()
                lines.append(f"Source: {title}\nURL: {url}\nSnippet: {snippet}")

            context = "\n\n".join(lines)
            return context
    except Exception as e:
        return f"Search error: {str(e)}"

# Quick test (Run this file directly to verify)
if __name__ == "__main__":
    from logger import logger

    logger.debug(fetch_live_info("Current weather in Kochi"))