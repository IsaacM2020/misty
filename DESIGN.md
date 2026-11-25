# EDITH design system: "Ink"

Written 2026-11-15 from the shipped SwiftUI app (`app/Misty`). It replaces the Pocket Operator and Highlighter looks. Mode: Operate. Isaac glances at EDITH many times a day, so every screen answers one question at a glance and keeps the words for tooltips.

## Feel

Clean, calm, a bit organic and fun to use every day. Matte surfaces with soft offset shadows, no glass. There is one living colour, iris, kept for her (the ink drop), for what's selected and for the one thing to press. Motion is springy, sound is small and synthesised.

Anti-references: dark-first or black-first looks, glowing pills, gradient text, rounded fonts, glass and blur used as decoration, generic AI-made UI, paragraphs of explainer text.

## Tokens (`Core/Theme.swift`)

| Role | Light | Dark |
|---|---|---|
| `bg` page | `#F6F7F6` | `#121314` |
| `desk` rail | `#EDEFEE` | `#18191B` |
| `panel` sunken (fields, chips, quiet keys) | `#ECEEED` | `#202224` |
| `raised` cards, composer | `#FFFFFF` | `#1B1C1E` |
| `ink` text and main button fill | `#15171A` | `#F1F2F3` |
| `dim` / `faint` | ink at 62% / 42% | |
| `line` hairline | ink at 8% | ink at 10% |
| `ember` iris fill | `#4F46F0` | `#8B83FF` |
| `accent` iris text | `#3B32D6` | `#A59EFF` |
| `emberSoft` iris wash | iris at 10% | iris at 18% |
| `good` / `urgent` / `warn` | `#13804F` / `#D2352B` / `#B25A09` | |
| `shadow` | `#1B2430` at 10% | black at 45% |

Source and subject colourways (`Plastic`: a soft wash plus a strong glyph): orange = mail and English, blue = calendar and Physics, green = WhatsApp and Chem, yellow = Messages, Spanish and Econ, iris-soft = Math, grey = other. `SubjectStyle` (in `Views/Keys.swift`) gives each school subject one icon and one colourway on every screen: Physics `atom`, Chem `flask.fill`, Math `function`, Econ `chart.line.uptrend.xyaxis`, English `book.fill`, Spanish `character.bubble.fill`.

Type: SF Pro (the system face). Headline 24/bold, title 17/semibold, body 13.5, small 12, labels 11.5–13 semibold. Numbers use `monospacedDigit`.

Radii: chips 6, small keys 7–8, list rows 10–12, cards 16, composer 22, buttons and pills are capsules. Shadows always sit below their item with a soft blur (radius 5–18, y 2–7). They grow on hover.

## Parts (`Views/Keys.swift`)

- **Orb**: her ink drop. Breathes when idle, wobbles while listening, turns while thinking, ripples while speaking, goes grey when paused. It shows up in the rail, in the gutter of each of her chat replies, in the "Now" header, in empty states, in loading states (thinking) and on the class gist.
- **GoButton / KeyStyle**: capsule keys that dip to 0.93 when pressed and lift to 1.03 on hover. Filled ink is the one main action on a row. Inbox Go keys add a glyph for what Go will do (calendar, draft, bell, cursor).
- **IconTile**: an SF Symbol in a rounded square tinted by its colourway. Used for sources, subjects, fact kinds and setup steps.
- **Card + cardRow**: lifted white block with a hairline and indented separators. Never nest cards.
- **Chip**: a count, a date or a state. **AreaTag**: a subject capsule with its icon.
- **Segmented**: two or three choices where the chosen one lifts.
- **PulseDots**: three iris dots that rise in turn while she works.
- **KeyHint**: keyboard shortcuts drawn as little keycaps.

## Rules that cut text

1. Each screen keeps one title and drops the "N open" or "N things" counts next to it (the rail badge already says it).
2. Explanations, reasons, costs, full notes and long errors go into `.help()` tooltips, not on screen.
3. A second line says one thing (who it's from, or when). Where the app is, how much she cares and how Go stands are shown with icons, bars and badges.
4. Long lists fold. Chat replies older than the latest fold to their opening with a `···` key. Priorities shows 3 per subject with a `+N` pill, and done items fold into one "N done" chip. Long class gists clamp to 4 lines until you tap them.
5. Banners are one-line capsules (icon, first clause, action) that sit at the top centre.
6. Empty states are the Orb plus 2–4 words.

## Motion and sound (`Core/Sound.swift`)

- Springs everywhere: presses (response 0.25–0.3, damping 0.55), hover (0.35/0.7), lists inserting and removing (0.4/0.85). Inbox rows slide out to the right when they're done. Chat messages rise in from below. Setup steps slide in from the right.
- Symbol transitions (`.contentTransition(.symbolEffect(.replace))`) on checkmarks.
- Sounds: `thunk` (bubble pop) when committing with Go or Start, `done` (two-note chirp) when ticking something done or copying, `down` pip on popover keys, plus `tick` and `turn` on the teaching board. All of them switch off with Settings > Button sounds.
- With Reduce Motion on, the Orb holds still.

## Screens

| Screen | One job | Shape |
|---|---|---|
| Chat | Talk to her | Greeting + big Orb + three icon starters when empty. Her replies have the Orb in the gutter, his sit in a tucked bubble. Rounded composer with an iris send key. |
| Inbox | Do what needs you | Now / Today / This week groups with count chips. The Now row is bigger. Row = source tile, 2-line task, sender name, status badge, hover-only snooze and more, tick and Go. |
| Priorities | Know what matters | Ranked list as numbered keys (1 in iris), area tags. "From class" grouped by subject, 3 each. "EDITH suggests" diff with +/− circles and check toggles. |
| Classes | Find and read a class | Subject icon filter, ask field, list of subject tile + date. Notes are cards: the gist in iris, To do, Where you got lost, Next, Remember, then sections. Time chips jump to the transcript. Bottom actions are icons. |
| Memory | See what she knows | Facts as a grid of tiles (kind icon, title, 3 lines, link chip, hover to forget). The Graph uses a capsule search. |
| Radar | See what she watches | Watching (1 line + clock chip), Told you (1 line + how/when glyphs + thumbs), Goals, Interests and People with 1–3 care bars, Lessons. |
| Settings | Change things | Grouped form with icon headers, no footers. Permissions with icons and check or block marks. Sources as tiles with health dots. Shortcuts as keycaps. |
| Menu bar popover | Quick start | Orb + status + spend. Three big keys (Talk, Inbox with badge, Class). An icon row for mute, pause, open, settings and quit. |
| Rail | Move around | Orb wordmark, filled symbol for the current screen, iris count badge, a hairline between daily and "knows" screens. Footer: status dot, spend, Retry. |
| Setup | Get connected | Orb header, step dots (the current one stretches), and per step a big icon key, a title and one line. |

## Known gaps

- The menu bar popover and the setup flow were checked by build only. They can't be captured with background screenshots (the popover is a menu bar extra, and setup only shows on a fresh install).
- `.impeccable/surfaces/app-misty-views-suggestionsview-swift.md` still describes Pocket Operator. It's stale and Ink replaces it.
- Settings > Permissions only shows when the permissions model has items.
