# FitFindr 🛍️

FitFindr is a thrift-shopping agent. Given a natural-language request (e.g.
*"vintage graphic tee under $30"*), it finds a matching secondhand listing,
suggests an outfit built around it using the user's wardrobe, and writes a
shareable OOTD caption for the look.

The agent orchestrates three tools through a planning loop that passes state
through a single `session` dict. See [`planning.md`](planning.md) for the
original spec and agent diagram this implementation was built from.

## Setup

**macOS / Linux:**
```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

**Windows:**
```bash
uv venv
.venv\Scripts\activate
uv pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (free key at [console.groq.com](https://console.groq.com)):
```
GROQ_API_KEY=your_key_here
```

**Run it:**
```bash
pytest                  # tool tests (mock Groq — no API key needed)
python agent.py         # CLI: happy-path + no-results scenarios
python app.py           # Gradio UI
```

The two LLM tools use Groq's `llama-3.3-70b-versatile`.

---

## Tool Inventory

The documented signatures below match the actual functions in [`tools.py`](tools.py).

### 1. `search_listings` — *find candidate listings*

```python
search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]
```

- **Purpose:** Search the 40-item mock dataset for secondhand clothing matching a
  keyword description, optional size, and optional price ceiling. Pure Python — no LLM.
- **Inputs:**
  - `description` (`str`): keywords describing the item, e.g. `"vintage graphic tee"`.
  - `size` (`str | None`): size to filter by, or `None` to skip. Case-insensitive
    substring match, so `"M"` matches both `"M"` and `"S/M"`.
  - `max_price` (`float | None`): inclusive price ceiling in USD, or `None` to skip.
- **Output:** `list[dict]` of matching listings, sorted by relevance (most keyword
  overlap first). Each dict has: `id` (str), `title` (str), `description` (str),
  `category` (str), `style_tags` (list[str]), `size` (str), `condition` (str),
  `price` (float), `colors` (list[str]), `brand` (str | None), `platform` (str).
  Returns `[]` if nothing matches — never raises.

### 2. `suggest_outfit` — *style the find*

```python
suggest_outfit(new_item: dict, wardrobe: dict) -> str
```

- **Purpose:** Use the LLM to suggest 1–2 complete outfits pairing the new item with
  the user's wardrobe. Falls back to general styling advice if the wardrobe is empty.
- **Inputs:**
  - `new_item` (`dict`): a listing dict (same shape returned by `search_listings`).
  - `wardrobe` (`dict`): dict with an `"items"` key holding a list of wardrobe items
    (each: `id`, `name`, `category`, `colors`, `style_tags`, optional `notes`). May be empty.
- **Output:** `str` — a non-empty outfit suggestion. Names specific wardrobe pieces
  when the wardrobe has items; gives general advice when it's empty.

### 3. `create_fit_card` — *write the caption*

```python
create_fit_card(outfit: str, new_item: dict) -> str
```

- **Purpose:** Use the LLM (at higher temperature, for variety) to write a casual,
  shareable 2–4 sentence Instagram/TikTok caption for the outfit.
- **Inputs:**
  - `outfit` (`str`): the suggestion string from `suggest_outfit`.
  - `new_item` (`dict`): the listing dict, used to mention title, price, and platform.
- **Output:** `str` — a 2–4 sentence caption mentioning the item name, price, and
  platform once each. Returns a sentinel error string (not an exception) if `outfit` is empty.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) runs a gated linear sequence.
It is **not** a fixed "call all three tools every time" pipeline — one branch can end
the run before the LLM tools are ever reached.

1. **Parse the query.** `_parse_query()` sends the query to the LLM and asks for JSON
   `{description, size, max_price}`. If the LLM call or JSON parse fails, it falls back
   to a regex parse so the agent stays usable. Result stored in `session["parsed"]`.
2. **Search — conditional branch.** Call `search_listings(description, size, max_price)`
   and store the list in `session["search_results"]`.
   - **If the list is empty** → set `session["error"]` to a helpful message and **`return`
     immediately**. `suggest_outfit` and `create_fit_card` are never called on this path.
   - **If not empty** → set `session["selected_item"] = search_results[0]` and continue.
3. **Suggest outfit.** Call `suggest_outfit(selected_item, wardrobe)` → store in
   `session["outfit_suggestion"]`. No branch here — the tool handles the empty-wardrobe
   case internally and always returns a usable string.
4. **Create fit card.** Call `create_fit_card(outfit_suggestion, selected_item)` → store
   in `session["fit_card"]`.
5. **Return** the session. The run is done when `fit_card` is set and `error` is `None`.

The only branch that changes which tools run is the empty-search-results check in step 2.

---

## State Management

All state for one interaction lives in a single `session` dict, created by
`_new_session(query, wardrobe)`. Tools never receive or mutate the session — each is a
pure function taking explicit inputs and returning a value. The planning loop reads from
and writes to the session between calls, threading each tool's output into the next tool's input.

| Field | Written by | Read by | Contains |
|---|---|---|---|
| `query` | `_new_session` | (record) | Raw user query |
| `wardrobe` | `_new_session` | step 3 | User's wardrobe dict (never modified) |
| `parsed` | step 1 | step 2 | `{description, size, max_price}` |
| `search_results` | step 2 | step 2 (empty check) | Ranked list of listing dicts |
| `selected_item` | step 2 | steps 3 & 4 | Top listing (`search_results[0]`) |
| `outfit_suggestion` | step 3 | step 4 | Outfit string |
| `fit_card` | step 4 | caller / UI | Caption string |
| `error` | step 2 (empty results) | caller / UI | Error string, else `None` |

The dependency chain is `parsed → search_results → selected_item → outfit_suggestion → fit_card`.
Each step consumes the previous step's output directly from the session.

---

## Error Handling and Fail Points

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No listing passes the filters, or all score 0 on keyword overlap | Returns `[]`. The loop sets `session["error"]` and returns early — the LLM tools are skipped. |
| `suggest_outfit` | `wardrobe["items"]` is empty (new user) | Tool detects the empty list and switches to a general-styling prompt; still returns a non-empty string. The loop needs no special case. |
| `create_fit_card` | `outfit` is empty / whitespace-only | Tool returns the sentinel `"Could not generate fit card: outfit description was empty."` without calling the LLM or raising. |

**Concrete example from testing** (`tests/test_tools.py`):

- `test_search_listings_no_results_returns_empty_list` calls
  `search_listings("designer ballgown", size="XXS", max_price=5.0)` and asserts it
  returns `[]`. Run through `run_agent`, this produces
  `session["error"] = "No listings found for 'designer ballgown' (XXS, under $5). Try broader keywords or a higher budget."`
  while `session["fit_card"]` stays `None` — confirming the early-return branch skips the LLM tools.
- `test_create_fit_card_empty_outfit_returns_sentinel` calls `create_fit_card("   ", new_item)`
  and asserts the exact sentinel string above, verifying no exception and no wasted API call.

The LLM-backed tests (`test_suggest_outfit_empty_wardrobe_uses_general_advice`) stub the
Groq client so the empty-wardrobe branch is verified deterministically and offline.

---

## Interaction Walkthrough

**User query:** *"I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"* (Example wardrobe)

*Before step 1, `_parse_query` extracts `{description: "vintage graphic tee", size: null, max_price: 30.0}`. The "baggy jeans / chunky sneakers" detail is styling context, not a search filter — those pieces are already in the example wardrobe.*

**Step 1 — Tool called:**
- Tool: `search_listings`
- Input: `description="vintage graphic tee"`, `size=None`, `max_price=30.0`
- Why this tool: nothing can be styled until we have a real listing to anchor the outfit.
- Output: top match `lst_002` — *"Y2K Baby Tee — Butterfly Print"*, $18, size S/M, depop (matches "vintage" + "graphic tee").

**Step 2 — Tool called:**
- Tool: `suggest_outfit`
- Input: `new_item=lst_002`, `wardrobe=example wardrobe` (baggy jeans, chunky sneakers, black denim jacket, …)
- Why this tool: the user asked how to style it; wardrobe is non-empty so we pair it with named pieces.
- Output: *"Tuck the butterfly baby tee into your baggy dark-wash jeans, add the vintage black denim jacket, and finish with the chunky white sneakers — a clean 90s throwback…"*

**Step 3 — Tool called:**
- Tool: `create_fit_card`
- Input: `outfit=<step 2 string>`, `new_item=lst_002`
- Why this tool: turn the styling into a shareable caption mentioning item, price, platform.
- Output: *"thrifted this Y2K butterfly baby tee off depop for $18 and i'm obsessed 🦋 tucked into baggy jeans with the denim jacket + chunky sneakers for an instant 90s moment…"*

**Final output to user:** the three Gradio panels show the listing (title, $18, condition, depop), the outfit idea, and the fit card. `session["error"]` is `None`.

---

## Spec Reflection

**One way `planning.md` helped during implementation:**
Writing the State Management table before any code meant the `session` dict's shape and
the read/write order were already settled. Implementing `run_agent` was mostly transcribing
that table into ordered assignments — I never had to stop and decide where a value should
live or how the next tool would reach it, and the tools stayed cleanly decoupled (pure
functions that only ever talk through the session).

**One divergence from the spec, and why:**
The planning.md walkthrough had the agent **ask the user for their size** when none was
given. In implementation I dropped the interactive prompt: `run_agent(query, wardrobe)` is
a single non-interactive call, and both the `python agent.py` test harness and the Gradio
textbox have no way to do a mid-run back-and-forth — a blocking `input()` would hang both.
So when size is absent the agent now proceeds with `size=None` (searches all sizes) instead
of asking. A future version could surface the size question at the UI layer before calling
`run_agent`, preserving the original intent without blocking the loop.

---

## AI Usage

I used Claude as the implementation tool throughout, directed by the specs in `planning.md`.
Two concrete instances:

**1. Implementing the planning loop — and overriding the size-prompt branch.**
I gave Claude the Planning Loop, State Management, and Architecture sections plus the three
finished tool signatures, and asked it to implement `run_agent`. The spec it was working from
called for asking the user for a size when none was parsed. I overrode this before accepting
the code: because `run_agent` is a single non-interactive call (and the test harness / Gradio
UI can't prompt mid-run), I had it proceed with `size=None` instead of blocking on input. I
also kept its `_parse_query` LLM call but added a regex fallback so the agent still runs if the
API is unavailable.

**2. Drafting the Planning Loop section — and rejecting the first, vague draft.**
When I first asked Claude to write the Planning Loop spec, it described the flow at a high
level ("decides which tool to call next"). I rejected that and directed it to spell out the
*actual conditional branches* — specifically the empty-`search_results` early return and the
exact `session` fields written at each step — so the section was implementable from the words
alone. The revised version is what the final `run_agent` was built from.

**3. Correcting the architecture diagram.**
Claude's first Mermaid diagram labeled all four steps as `read/write` against the session.
I corrected step 1 to **write-only**, since it reads the raw `query` from the function
argument (not the session) and only writes `parsed` back — a small accuracy fix I caught on review.