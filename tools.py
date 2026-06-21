"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()

    # Tokenize the description into lowercase keywords, dropping tiny stopwords
    # so common filler words don't inflate the relevance score.
    stopwords = {"a", "an", "the", "for", "and", "or", "in", "of", "to", "is"}
    keywords = [
        word
        for word in re.findall(r"[a-z0-9]+", description.lower())
        if word not in stopwords
    ]

    scored: list[tuple[int, dict]] = []
    for listing in listings:
        # Price filter (inclusive).
        if max_price is not None and listing["price"] > max_price:
            continue

        # Size filter — case-insensitive substring so "M" matches "S/M".
        if size is not None and size.lower() not in listing["size"].lower():
            continue

        # Score by keyword overlap against title, description, and style_tags.
        haystack = " ".join(
            [
                listing["title"],
                listing["description"],
                " ".join(listing["style_tags"]),
            ]
        ).lower()
        score = sum(1 for word in keywords if word in haystack)

        # Drop listings with no relevant match.
        if score > 0:
            scored.append((score, listing))

    # Highest score first; ties keep dataset order (stable sort).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [listing for _, listing in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_line = (
        f"{new_item['title']} — a {new_item['category']} "
        f"({', '.join(new_item['style_tags'])}); colors: {', '.join(new_item['colors'])}"
    )

    items = wardrobe.get("items", [])
    if items:
        # Wardrobe-pairing path: list the user's pieces and ask for specific combos.
        wardrobe_lines = "\n".join(
            f"- {it['name']} ({it['category']}; "
            f"{', '.join(it.get('colors', []))}; {', '.join(it.get('style_tags', []))})"
            for it in items
        )
        prompt = (
            f"A user is considering buying this secondhand item:\n{item_line}\n\n"
            f"Here is their existing wardrobe:\n{wardrobe_lines}\n\n"
            "Suggest 1-2 complete outfit combinations that pair the new item with "
            "specific named pieces from their wardrobe. Reference the wardrobe items "
            "by name. Keep it concise and practical."
        )
    else:
        # Empty-wardrobe path: general styling advice, no wardrobe references.
        prompt = (
            f"A user is considering buying this secondhand item:\n{item_line}\n\n"
            "They have not entered any wardrobe yet. Give general styling advice: "
            "what kinds of pieces pair well with it, what colors complement it, and "
            "what overall vibe it suits. Keep it concise and practical."
        )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are a thoughtful personal stylist for secondhand fashion.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        suggestion = response.choices[0].message.content.strip()
        if suggestion:
            return suggestion
    except Exception:
        pass

    # Fallback so the agent can still proceed to create_fit_card.
    return "Could not generate outfit suggestions right now. Try describing your wardrobe."


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # Guard against an empty or whitespace-only outfit — return a sentinel
    # string rather than raising, so the agent loop completes gracefully.
    if not outfit or not outfit.strip():
        return "Could not generate fit card: outfit description was empty."

    prompt = (
        "Write a short, casual Instagram/TikTok-style caption (2-4 sentences) for an "
        "outfit built around a thrifted find. Make it feel like an authentic OOTD post, "
        "not a product description.\n\n"
        f"Item: {new_item['title']}\n"
        f"Price: ${new_item['price']}\n"
        f"Platform: {new_item['platform']}\n"
        f"Outfit: {outfit}\n\n"
        "Mention the item name, the price, and the platform once each, naturally. "
        "Capture the specific vibe of the outfit. Keep it lowercase and conversational."
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You write fun, authentic secondhand-fashion captions.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,  # higher temperature → varied captions per run
        )
        caption = response.choices[0].message.content.strip()
        if caption:
            return caption
    except Exception:
        pass

    return "Could not generate fit card right now. Please try again."
