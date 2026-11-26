# Output Template & Rumination Rubric

Always write the digest in **the same language the user used** when invoking `ruminate`.

## 1. What Makes an Item Worth Ruminating? (7 High-Value Signals)

Prioritize behavioral and cognitive value over raw transcript length. Use the extracted `signals` metadata (`turn_count`, `user_char_ratio`, `ends_with_open_question`) as hints alongside semantic reading.

1. **Decision Shift** (*Value of Information*): Helped change direction, rule out a flawed option, or supply a concrete basis for the next decision.
2. **Model Update** (*Bayesian Surprise*): Overturned a previously taken-for-granted assumption or rewrote a mental model.
3. **Sharp Knowledge Gap** (*Active Inference / Information-Gap*): No immediate deliverable or conclusion yet, but precisely exposed a boundary between what the user thought they knew and where they got stuck.
4. **High-Leverage Inference** (*Relevance Theory*): High information density and low recall friction, or combines a new fragment with prior background to yield a fresh conclusion.
5. **Cross-Domain Bridge** (*Structural Holes*): Imported a concept or method from Domain A into Domain B, creating a non-redundant collision across disciplines or projects.
6. **Serendipitous Seed** (*Zettelkasten / Tangent*): A casual remark or off-topic detour (often hidden inside an unrelated routine session) that can spark new connections when revisited days later.
7. **Open Loop & Latent Pattern** (*Zeigarnik / Retrospective Sensemaking*): An exploratory thread cut short mid-way without closure, OR an implicit recurring focus/tension across multiple conversations or apps that only becomes visible in hindsight.

## 2. High-Disguise Noise Exclusions

Do **not** promote these to main Memory Cue Cards:
- **Long Debugging Marathons**: 30–50 turns resolving one environment/dependency bug unless a reusable principle emerged.
- **Delegated One-Off Chores**: Translation, email polishing, formatting, or throwaway scripts.
- **Pure Lookups**: Syntax checks, CLI flags, or error code explanations.

## 3. Admission & Merge Rules

- **Cross-app merge**: Merge across all apps by theme and value. **Never** split the main section into per-app silos.
- **Strict cap**: Keep the main section to **3 to 7 Memory Cue Cards** so recall effort stays low.
- **Small-week auto-simplification**: If fewer than 3 cards surface, omit empty **Thinking Radar** or **Edge Index** blocks rather than printing boilerplate shells.

## 4. Default Output Layout (Layout C: Thinking Radar + Memory Cue Cards + Edge Index)

Every card must include a traceability anchor:
`[App | YYYY-MM-DD | Session Title or ID]` (use a Markdown link when `url` is present, e.g. `[Gemini | 2026-11-24 | Topic](https://gemini.google.com/app/c/...)`).

```markdown
# Ruminate (YYYY-MM-DD to YYYY-MM-DD)

## Thinking Radar
- **Cross-app motifs & latent tensions**: <1–2 recurring patterns or tensions surfaced across multiple sessions/apps>
- **Coverage**: <N> sources, <M> sessions -> <K> cue cards, <X> routine sessions folded

## Memory Cue Cards

### 1. <Short Title> `<Signal Tag>`
- **Your exact prompt**: "<Verbatim user quote, <= 60–80 chars, to trigger episodic memory>"
- **Where it landed**: <1–2 factual sentences on conclusions reached or where/why it paused>
- **Re-entry angle**: <1 concrete follow-up question or 15-min probe; for pure knowledge gaps, the sharpest boundary question to test next>
- **Source**: `[App | YYYY-MM-DD | Session Title or ID]`

## Edge Index (Optional)
| Date | Source | One-line topic |
| :--- | :--- | :--- |
| MM-DD | App | ... |

---
*Filtered out X routine sessions (App1: A, App2: B). Ask for "full mode" to expand all sessions.*
```

If no genuine items surface in the window, write a single honest line (e.g., `None surfaced in this window.`) rather than padding with routine tasks.

## 5. Full Recap Format (`full` mode)

Use only when the user explicitly asks for a full/complete summary:

```markdown
# Ruminate Full Recap (YYYY-MM-DD to YYYY-MM-DD)

## Highlights & Sparks
- ... `[App | YYYY-MM-DD | Session Title or ID]`

## By Topic
### <Topic Name>
- **<Session Summary>**: <1-3 sentences> `[App | YYYY-MM-DD | Session Title or ID]`
```

## 6. Opt-in File Saving

- Deliver in chat by default.
- Only write to disk if the user explicitly asks to save/export.
- Filename: `ruminate-YYYY-MM-DD.md`. If it already exists, increment to `ruminate-YYYY-MM-DD-v2.md`, `ruminate-YYYY-MM-DD-v3.md`, etc. Never overwrite an existing file.
