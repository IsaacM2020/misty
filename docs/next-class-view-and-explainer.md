# Next: class view (online vs in person) and the explainer window

Written 24 Oct 2026 from Isaac's voice notes. Section 1 steps 1-3 are built (system audio, online/in-person, shared-screen text) and section 2's first version is built (the `draw` tool and window, see ARCHITECTURE.md section 11 and "Class view"); diarisation for rooms is not.

## 1. Class view: any platform, teacher and student apart

What exists: Class Notes listens through the microphone only (`listenForClass`). A teacher on Zoom, Meet, Teams or a whiteboard app comes out of the speakers, so the mic hears them badly or not at all. That is the "Zoom teacher isn't heard" bug.

Plan (Granola's approach, which is platform-blind because it records the Mac's audio, not the app):
- **Online class:** capture system audio with a Core Audio process/system tap (macOS 14.2+, needs the "System Audio Recording" permission), plus the mic. System audio is labelled Teacher (and classmates), mic is Me. No Zoom/Meet/Teams integration, so a new platform needs nothing.
- **In-person class:** mic only, one mixed stream. Speaker separation by voice (diarisation) after the fact, with the teacher picked as the longest-talking voice, Isaac's own voice enrolled once so Me is exact.
- **How Misty knows which:** a toggle on Start class (Online / In person), defaulted from the Google Calendar event (a meeting link means online). The class notes keep the mode and show it.
- **Analysis differs:** online classes also have the shared screen (slides): sample frames when the picture changes and note them next to what was said. In-person has no screen, so the notes lean on the audio and any photo of the board Isaac takes.
- Ears: Phonon-2 on both streams, already on-device; two instances or one with a stream label.
- Build order: system-audio tap + labels (the real bug) -> online/in-person switch -> screen frames -> diarisation for rooms.
- Test without a class: play a recorded lesson through the speakers while talking into the mic, check the labels.

## 2. Explainer window: Misty draws while she explains

Goal: ask about economics, maths or physics and a window appears on screen where Misty draws and animates the idea in step with her voice. Ultra efficient, still beautiful.

Options weighed:
- **Manim itself:** best looking, but needs LaTeX, Cairo and ffmpeg, renders a video in seconds to minutes, and cannot react while she talks. Too heavy and too slow for "in real time".
- **Native scenes (recommended):** the model writes a small JSON scene (axes, curves, shapes, labels, equations, "step N reveals these things"), the app draws it with SwiftUI Canvas at 60 fps, and each step lines up with a sentence she says. Maths text through a Swift LaTeX renderer (SwiftMath). Costs one short model call, no install, no render wait, works offline for the drawing.
- Starter library of scene pieces that cover most IB questions: function graphs and transformations, supply/demand with shifts and ceilings/floors, vectors and forces, number lines, step-by-step algebra with the changing line highlighted.
- A window of its own, floating, that she can keep open and update ("now shift demand right").
- Efficiency: scene JSON is tiny (a few hundred tokens), cached per question, drawing is local. Quality comes from the scene library and easing, not from rendering video.
- First milestone: supply and demand with a price ceiling (a question he asked this week) and f(x) = 3x/(x-2) with its asymptotes, driven by a typed scene, then let the model write the scenes.

### Status of section 2 (24 Oct, night)

Built the recommended native-scenes route: the model writes the scene as a `draw` tool call, the engine checks it and samples the curves, the app draws it in its own floating window with step-by-step reveal. Done: axes, curves with asymptote breaks, dashed lines, dots, arrows, filled areas, equation cards with a highlighted line, tables. Still to do:
- (Done 24 Oct night) Step captions are spoken in step with the picture in tap-to-talk answers.
- Real maths typesetting (SwiftMath) instead of plain "x²" text.
- Ready-made scene helpers for the common IB pictures (supply and demand with a shift, ceiling or floor; vectors and forces) so the model writes less.
- "Draw on the screen's question": look at the question, then draw it, in one turn, without the model re-reading the picture.
