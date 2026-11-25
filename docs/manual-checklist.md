# Manual checklist for Misty.app (walk it with Isaac at the end of M6)

Start clean: `mv ~/Misty ~/Misty.bak` (keep the real index safe), then open `~/Applications/Misty.app`.

- [ ] Menu bar icon appears, no Dock icon.
- [ ] Setup: privacy screen says what leaves the Mac, ZDR line present.
- [ ] Setup: paste the OpenRouter key, "Save and test" shows ZDR confirmed. (This is the first paid call.)
- [ ] Setup: Full Disk Access step turns green within a few seconds of granting.
- [ ] Setup: Google with the wrong account (other-account) is refused; the right one connects.
- [ ] Setup: chats list shows real WhatsApp and iMessage chats; excluded ones stay excluded.
- [ ] Setup: estimate is shown with the cap; over the cap blocks the build button.
- [ ] Wi-Fi off: strip goes red "Offline: queued, will resume"; Wi-Fi on: resumes by itself.
- [ ] Revoke Full Disk Access: banner "Misty needs Full Disk Access" appears.
- [ ] Lower the daily cap to $0.05 and trigger work: "Cap hit" banner with Raise cap button.
- [ ] Suggestions: Done, Snooze (1h, tonight, tomorrow), Dismiss, Open all do what they say.
- [ ] Priorities: weekly diff shows adds/removes; accept some, Apply keeps the rest.
- [ ] Graph: you in the centre, areas in a ring, recent activity glows, click opens a page,
      "This is wrong" appends a line to ~/Misty/wiki/corrections.md.
- [ ] Settings: Pause everything pauses the queue; Delete all (typed DELETE) removes ~/Misty and every
      `ai.misty` Keychain item.
- [ ] Quit Misty: no `misty_engine` process is left running.
- [ ] Graph (quality pass): the first view has Me plus your life areas in a ring (under 60 nodes); clicking an area opens its top 12 with a "+N" bubble; search finds a name and opens its area; labels do not overlap badly.
- [ ] Settings > Choose chats: the lock on a chat keeps it out of answers, summaries and the graph at once (open a page that cited it and check the lines are gone).
- [ ] Page panel: "This is the same as..." merges two names and "Undo merge" brings the second one back with its page.
