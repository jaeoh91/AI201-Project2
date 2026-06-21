"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json
import re

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    _get_groq_client,
)


# ── query parsing ─────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """
    Extract search parameters from a natural-language query.

    Uses the LLM to return {description, size, max_price} as JSON — this handles
    conversational phrasing ("around thirty bucks", "I'm usually a medium") that
    regex would miss. Falls back to a simple regex parse if the LLM call or JSON
    parsing fails, so the agent stays usable even when the API is unavailable.
    """
    prompt = (
        "Extract search parameters from this query and return ONLY valid JSON "
        "with exactly these keys:\n"
        '- "description": a short keyword phrase for the item (e.g. "vintage graphic tee")\n'
        '- "size": the clothing size as a string (e.g. "M"), or null if not mentioned\n'
        '- "max_price": the maximum price as a number (e.g. 30.0), or null if not mentioned\n\n'
        f'Query: "{query}"'
    )
    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You extract structured search "
                 "parameters and reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        # Pull the first {...} block in case the model wraps it in prose/fences.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group(0) if match else text)
        max_price = data.get("max_price")
        return {
            "description": (data.get("description") or query).strip(),
            "size": data.get("size") or None,
            "max_price": float(max_price) if max_price is not None else None,
        }
    except Exception:
        return _parse_query_fallback(query)


def _parse_query_fallback(query: str) -> dict:
    """Regex-based parse used when the LLM parse is unavailable."""
    max_price = None
    price_match = re.search(r"under\s+\$?\s*(\d+(?:\.\d+)?)", query, re.IGNORECASE)
    if price_match:
        max_price = float(price_match.group(1))

    size = None
    size_match = re.search(r"size\s+([A-Za-z0-9/]+)", query, re.IGNORECASE)
    if size_match:
        size = size_match.group(1).upper()

    return {"description": query, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: fresh session — the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2: parse the query into description / size / max_price.
    session["parsed"] = _parse_query(query)
    description = session["parsed"]["description"]
    size = session["parsed"]["size"]
    max_price = session["parsed"]["max_price"]

    # Step 3: search. BRANCH — if nothing matches, set error and return early.
    # suggest_outfit / create_fit_card are deliberately NOT called on this path.
    session["search_results"] = search_listings(description, size, max_price)
    if not session["search_results"]:
        size_txt = size if size else "any size"
        price_txt = f"under ${max_price:g}" if max_price is not None else "any price"
        session["error"] = (
            f"No listings found for '{description}' ({size_txt}, {price_txt}). "
            "Try broader keywords or a higher budget."
        )
        return session

    # Step 4: select the top-ranked listing to carry forward.
    session["selected_item"] = session["search_results"][0]

    # Step 5: suggest an outfit using the selected item + wardrobe.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6: turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: done — error stays None on the happy path.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
