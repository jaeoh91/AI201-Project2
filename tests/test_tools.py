"""
tests/test_tools.py

One test per documented failure mode, plus a few sanity checks.

The LLM tools (suggest_outfit, create_fit_card) are tested without hitting the
Groq API: the empty-outfit guard returns before any network call, and the
empty-wardrobe path is exercised with a stubbed Groq client so the test is
deterministic and runs offline.

Run from the project root with:
    pytest
"""

from tools import search_listings, suggest_outfit, create_fit_card


# ── Stub Groq client (keeps LLM tests offline + deterministic) ──────────────────

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content, captured):
        self._content = content
        self._captured = captured

    def create(self, **kwargs):
        # Record the call so the test can inspect the prompt that was sent.
        self._captured.update(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content, captured):
        self.completions = _FakeCompletions(content, captured)


class _FakeClient:
    def __init__(self, content, captured):
        self.chat = _FakeChat(content, captured)


# ── search_listings ─────────────────────────────────────────────────────────────

def test_search_listings_no_results_returns_empty_list():
    """Failure mode: no listing matches the query → return [], do not raise."""
    results = search_listings("designer ballgown", size="XXS", max_price=5.0)
    assert results == []


def test_search_listings_happy_path_finds_graphic_tee():
    """A 'vintage graphic tee' under $30 should surface the Y2K butterfly tee."""
    results = search_listings("vintage graphic tee", size="M", max_price=30.0)
    assert len(results) > 0
    assert results[0]["id"] == "lst_002"


def test_search_listings_respects_price_ceiling():
    """Every returned listing must be at or under max_price."""
    results = search_listings("vintage", max_price=25.0)
    assert results  # something matches
    assert all(listing["price"] <= 25.0 for listing in results)


# ── suggest_outfit ───────────────────────────────────────────────────────────────

def test_suggest_outfit_empty_wardrobe_uses_general_advice(monkeypatch):
    """Failure mode: empty wardrobe → general styling advice, no crash."""
    captured = {}
    monkeypatch.setattr(
        "tools._get_groq_client",
        lambda: _FakeClient("Pair it with neutral basics for an easy everyday look.", captured),
    )

    new_item = {
        "title": "Y2K Baby Tee — Butterfly Print",
        "category": "tops",
        "style_tags": ["y2k", "graphic tee"],
        "colors": ["white", "pink"],
    }
    result = suggest_outfit(new_item, {"items": []})

    assert isinstance(result, str)
    assert result.strip()  # non-empty
    # The empty-wardrobe prompt should ask for general advice, not name pieces.
    prompt = captured["messages"][-1]["content"]
    assert "general styling advice" in prompt.lower()


def test_suggest_outfit_with_wardrobe_references_pieces(monkeypatch):
    """Non-empty wardrobe → the prompt lists the user's actual pieces by name."""
    captured = {}
    monkeypatch.setattr(
        "tools._get_groq_client",
        lambda: _FakeClient("Tuck the tee into your baggy jeans.", captured),
    )

    new_item = {
        "title": "Y2K Baby Tee",
        "category": "tops",
        "style_tags": ["y2k"],
        "colors": ["white"],
    }
    wardrobe = {"items": [{"name": "Baggy jeans", "category": "bottoms",
                           "colors": ["blue"], "style_tags": ["denim"]}]}
    result = suggest_outfit(new_item, wardrobe)

    assert result.strip()
    prompt = captured["messages"][-1]["content"]
    assert "Baggy jeans" in prompt


# ── create_fit_card ──────────────────────────────────────────────────────────────

def test_create_fit_card_empty_outfit_returns_sentinel():
    """Failure mode: empty/whitespace outfit → sentinel string, no API call, no raise."""
    new_item = {"title": "Y2K Baby Tee", "price": 18.0, "platform": "depop"}
    result = create_fit_card("   ", new_item)
    assert result == "Could not generate fit card: outfit description was empty."


def test_create_fit_card_happy_path(monkeypatch):
    """A real outfit string → returns the model's caption."""
    captured = {}
    monkeypatch.setattr(
        "tools._get_groq_client",
        lambda: _FakeClient("thrifted this tee off depop for $18 🦋", captured),
    )
    new_item = {"title": "Y2K Baby Tee", "price": 18.0, "platform": "depop"}
    result = create_fit_card("tee + baggy jeans + chunky sneakers", new_item)
    assert "depop" in result