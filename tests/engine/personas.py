"""Made-up people used to test life-area synthesis. None of them is a real person and none is the first user.

Each persona is a list of hidden themes. A theme has entities and notes; the model only ever sees the notes and
entities, never the theme labels. `expect` is a loose rubric: words, any one of which in some area's name or
description counts as "that part of the life was found". Coverage of the rubric is reported, not asserted
to be perfect, because naming is the model's call.
"""
from __future__ import annotations

import re

from misty_engine.wiki.areas import EntityRec, NoteRec
from misty_engine.wiki.notes import slugify

FOLDER = {"person": "people", "project": "projects", "topic": "topics"}


def _theme(expect, ents, notes):
    return {"expect": expect, "ents": ents, "notes": notes}


PERSONAS = {
    "nurse": {
        "about": "Priya, 38, intensive care nurse in Leeds. Part-time nursing degree. Married to Dan, two kids. "
                 "Sings in a church choir, training for a half marathon, renovating the kitchen.",
        "themes": [
            _theme(["hospital", "icu", "ward", "shift", "patient", "nursing work", "clinical work"],
                   [("project", "ICU rota"), ("topic", "handover"), ("person", "Sister Okafor"), ("topic", "night shifts")],
                   ["Shift swap request for Thursday|gmail|2026-09-30|Priya asks Okafor to swap the Thursday night shift.",
                    "ICU handover checklist|files|2026-09-12|Checklist used at the 7am handover on the ward.",
                    "Rota October v3|files|2026-09-25|Four night shifts and two long days are planned in October.",
                    "Overtime approval|gmail|2026-10-02|Ward manager approves two extra shifts for the month.",
                    "Patient safety refresher|calendar|2026-10-08|Mandatory refresher at the hospital, 2 hours.",
                    "Sepsis protocol notes|files|2026-08-19|Notes from the sepsis pathway training for ICU staff."]),
            _theme(["degree", "study", "course", "university", "assignment", "education", "student"],
                   [("project", "Nursing degree"), ("topic", "pharmacology"), ("person", "Dr Whitfield"), ("topic", "placement")],
                   ["Pharmacology essay draft|files|2026-10-03|Essay on drug interactions in critical care, 1800 words.",
                    "Assignment 2 deadline|calendar|2026-10-20|Pharmacology essay due to Dr Whitfield.",
                    "Placement hours log|files|2026-09-15|Logged 42 hours of community placement this term.",
                    "Reading list term 2|files|2026-09-01|Textbooks for the advanced practice module.",
                    "Feedback from Dr Whitfield|gmail|2026-09-22|Feedback on the reflective piece: stronger evidence needed.",
                    "Exam timetable|files|2026-10-05|Written exam on 12 December, two hours."]),
            _theme(["family", "kids", "children", "household", "home life", "parenting"],
                   [("person", "Dan"), ("person", "Mia"), ("person", "Leo"), ("topic", "school run")],
                   ["School run rota with Dan|imessage|2026-10-01|Dan takes Mia and Leo Monday to Wednesday.",
                    "Mia parents evening|calendar|2026-10-14|Parents evening at 4:30pm, bring the reading folder.",
                    "Leo football fixtures|files|2026-09-28|Saturday matches at 10am through November.",
                    "Birthday party plan for Leo|imessage|2026-09-18|Soft play booking for eight children.",
                    "Dentist for the kids|gmail|2026-09-20|Check-ups booked for both children on 3 November.",
                    "Weekly meal plan|files|2026-10-06|Menu and shopping list for the week."]),
            _theme(["church", "choir", "faith", "music", "community", "singing"],
                   [("project", "Church choir"), ("person", "Father Daniel"), ("topic", "Christmas concert")],
                   ["Choir rehearsal Wednesday|calendar|2026-10-07|7:30pm rehearsal for the Christmas concert.",
                    "Christmas concert programme|files|2026-10-01|Eight carols, soprano part marked for Priya.",
                    "Message from Father Daniel|imessage|2026-09-27|Asks the choir to learn two new hymns.",
                    "Choir WhatsApp rota|imessage|2026-09-10|Who brings tea and biscuits each week.",
                    "Sheet music scans|files|2026-08-30|Scanned parts for Ave Maria and Silent Night.",
                    "Harvest service|calendar|2026-10-04|Choir leads the 10am service."]),
            _theme(["run", "marathon", "fitness", "training", "exercise", "health"],
                   [("project", "Half marathon"), ("topic", "running plan"), ("person", "Coach Jamie")],
                   ["12 week half marathon plan|files|2026-08-25|Long runs on Sundays building to 20 km.",
                    "Long run log|files|2026-10-05|16 km in 1h 38 on the canal route.",
                    "Physio appointment|calendar|2026-10-09|Knee check with the physio.",
                    "Race entry confirmation|gmail|2026-08-30|Entry confirmed for the Leeds half in November.",
                    "Trainers order|gmail|2026-09-02|New running shoes shipped.",
                    "Parkrun results|gmail|2026-10-03|25 minutes 41 seconds, personal best."]),
            _theme(["house", "home", "renovation", "mortgage", "property", "kitchen", "finance"],
                   [("project", "Kitchen renovation"), ("person", "Builder Sam"), ("topic", "mortgage")],
                   ["Kitchen quote from Sam|gmail|2026-09-05|Quote for cabinets and worktop, 9,400 pounds.",
                    "Mortgage remortgage options|files|2026-09-14|Comparison of two fixed-rate deals.",
                    "Tile samples|imessage|2026-09-29|Dan prefers the grey tiles, Priya the white.",
                    "Planning of work weeks|files|2026-10-01|Builders start 2 November, kitchen out for three weeks.",
                    "Electrician booking|gmail|2026-10-06|Electrician booked for first fix.",
                    "Building insurance renewal|gmail|2026-09-19|Renewal quote arrives next month."]),
            _theme(["travel", "trip", "holiday", "hometown", "visit"],
                   [("topic", "Lagos trip"), ("person", "Aunt Ngozi")],
                   ["Flights to Lagos|gmail|2026-08-20|Four return tickets for December.",
                    "Visa documents checklist|files|2026-09-08|Passport validity and vaccination certificates.",
                    "Gifts list for family|files|2026-09-30|Presents for Aunt Ngozi and cousins.",
                    "Airport transfer|gmail|2026-10-02|Driver confirmed for the arrival night.",
                    "Packing list|files|2026-10-07|Warm clothes for the layover, light clothes for Lagos.",
                    "Travel insurance|gmail|2026-09-11|Family policy for 3 weeks."]),
        ],
    },
    "founder": {
        "about": "Marcus, 34, cofounder and CEO of a logistics software startup in Austin. One baby daughter. "
                 "Lifts weights, angel invests a little, coaches a youth robotics team.",
        "themes": [
            _theme(["product", "engineering", "software", "platform", "roadmap", "build"],
                   [("project", "Routing engine"), ("topic", "roadmap"), ("person", "Tanvir"), ("topic", "API v2")],
                   ["Routing engine v2 spec|files|2026-09-10|Rewrite of the route optimiser to cut compute cost.",
                    "Q4 roadmap|files|2026-09-28|Three bets: live tracking, driver app, API v2.",
                    "Incident postmortem|files|2026-10-01|Outage caused by a queue backlog, fix shipped.",
                    "Sprint review notes|files|2026-10-09|Release 4.2 shipped, API v2 slips a week.",
                    "Tanvir design review|calendar|2026-10-12|Review of the driver app flows.",
                    "Tech debt list|files|2026-08-22|Eleven items ranked by risk."]),
            _theme(["fundrais", "investor", "seed", "series", "capital", "funding"],
                   [("project", "Series A"), ("person", "Helena Park"), ("topic", "pitch deck"), ("person", "Northlight Capital")],
                   ["Series A pitch deck v7|files|2026-09-25|Deck with new traction numbers and market slide.",
                    "Helena Park intro call|calendar|2026-10-06|Partner at Northlight, 45 minute intro.",
                    "Data room checklist|files|2026-10-02|Financials, cap table and customer contracts.",
                    "Term sheet comments|gmail|2026-10-10|Lawyer comments on liquidation preference.",
                    "Investor update September|gmail|2026-10-01|Monthly update: revenue up 14 percent.",
                    "Fundraising tracker|files|2026-09-18|Twenty one funds contacted, six second meetings."]),
            _theme(["hiring", "recruit", "team", "people", "candidates", "talent"],
                   [("project", "Hiring plan"), ("topic", "interviews"), ("person", "Rosa Delgado")],
                   ["Hiring plan Q4|files|2026-09-12|Two backend engineers and one account executive.",
                    "Interview scorecard|files|2026-09-16|Rubric for system design interviews.",
                    "Candidate debrief|calendar|2026-10-03|Debrief for the senior engineer finalist.",
                    "Offer letter draft|gmail|2026-10-08|Offer for Rosa Delgado as head of operations.",
                    "Referral bonus policy|files|2026-08-29|Referral bonus set at 3,000 dollars.",
                    "Onboarding checklist|files|2026-09-05|First week plan for new engineers."]),
            _theme(["customer", "sales", "pipeline", "clients", "accounts", "revenue"],
                   [("project", "Enterprise pipeline"), ("person", "Gulf Freight"), ("topic", "renewals")],
                   ["Gulf Freight contract renewal|gmail|2026-09-21|Renewal at a 12 percent uplift pending sign-off.",
                    "Pipeline review|files|2026-10-04|Nine enterprise deals, three in negotiation.",
                    "Pilot results for Harbor Co|files|2026-09-30|Pilot cut delivery time by 11 percent.",
                    "Pricing change memo|files|2026-09-07|Per-vehicle pricing replaces flat fee.",
                    "QBR deck for Gulf Freight|files|2026-10-07|Quarterly business review slides.",
                    "Churn analysis|files|2026-09-14|Two small customers left over onboarding issues."]),
            _theme(["legal", "cofounder", "company", "equity", "incorporat", "governance", "board"],
                   [("person", "Anita Rao"), ("topic", "cap table"), ("project", "Board meeting")],
                   ["Board deck October|files|2026-10-05|Metrics, hiring and fundraising update for the board.",
                    "Cofounder equity refresh|files|2026-09-02|Proposal to refresh vesting for both founders.",
                    "Option pool expansion|gmail|2026-09-26|Lawyer drafts the board consent.",
                    "Annual filing reminder|gmail|2026-09-15|Delaware franchise tax due in March.",
                    "Anita Rao 1:1|calendar|2026-10-01|Weekly sync with cofounder Anita.",
                    "Insurance renewal|gmail|2026-09-23|D and O renewal quotes arrive."]),
            _theme(["family", "baby", "daughter", "home", "parent", "wife"],
                   [("person", "Jess"), ("person", "Nora"), ("topic", "daycare")],
                   ["Nora pediatrician visit|calendar|2026-10-02|Six month checkup.",
                    "Daycare waitlist|gmail|2026-09-08|Spot confirmed from January.",
                    "Date night with Jess|imessage|2026-09-27|Dinner booked Saturday, sitter confirmed.",
                    "Family photos album|files|2026-09-20|Photos from the lake weekend.",
                    "Grandparents visit|calendar|2026-10-18|Jess's parents fly in.",
                    "Baby proofing list|files|2026-08-31|Outlets, stairs and cabinets."]),
            _theme(["fitness", "lift", "gym", "health", "training", "workout"],
                   [("project", "Strength program"), ("topic", "gym log"), ("person", "Trainer Dev")],
                   ["Strength program week 6|files|2026-10-06|Squat 140 kg for five reps.",
                    "Gym log September|files|2026-09-30|Four sessions a week, one missed.",
                    "Trainer Dev session|calendar|2026-10-10|Technique check on deadlifts.",
                    "Blood test results|gmail|2026-09-17|Vitamin D slightly low.",
                    "Sleep tracker summary|files|2026-10-03|Average 6h 20, resting heart rate 56.",
                    "Meal prep plan|files|2026-09-09|High protein lunches for the week."]),
        ],
    },
    "student": {
        "about": "Mei, 17, final year of high school in Vancouver. Debate captain, plays violin in the youth "
                 "orchestra, works weekends at a cafe, applying to universities.",
        "themes": [
            _theme(["debate", "tournament", "speech", "argument"],
                   [("project", "Debate team"), ("topic", "provincial championship"), ("person", "Coach Alvarez")],
                   ["Provincials case file|files|2026-10-02|Motion on carbon pricing, three contentions.",
                    "Practice round feedback|files|2026-09-24|Coach Alvarez flags speed and signposting.",
                    "Tournament schedule|calendar|2026-10-18|Rounds 1 to 4 on Saturday.",
                    "Novice mentoring plan|files|2026-09-12|Weekly sessions with two new members.",
                    "Cross examination drills|files|2026-09-30|Question bank by theme.",
                    "Travel forms for provincials|gmail|2026-10-05|Permission slip and hotel list."]),
            _theme(["music", "violin", "orchestra", "practice", "concert"],
                   [("project", "Youth orchestra"), ("topic", "violin repertoire"), ("person", "Ms Tran")],
                   ["Orchestra rehearsal schedule|calendar|2026-09-15|Tuesdays 6pm until the winter concert.",
                    "Brahms symphony parts|files|2026-09-20|Second violin parts with bowings.",
                    "Lesson notes with Ms Tran|files|2026-10-01|Work on shifting and vibrato.",
                    "Audition recording|files|2026-09-05|Recording for the regional chair.",
                    "Concert tickets|gmail|2026-10-08|Family tickets for the December concert.",
                    "Practice log|files|2026-10-04|One hour a day, scales first."]),
            _theme(["job", "work", "cafe", "shift", "money", "earning"],
                   [("project", "Cafe job"), ("person", "Manager Ravi"), ("topic", "weekend shifts")],
                   ["Weekend shift roster|files|2026-10-03|Saturday and Sunday mornings.",
                    "Ravi asks for extra shift|imessage|2026-09-28|Cover for Sunday lunch.",
                    "Pay stub September|files|2026-10-01|Eleven shifts, 640 dollars.",
                    "Barista training|calendar|2026-09-07|Espresso training session.",
                    "Tip jar split|imessage|2026-09-19|Team agrees to weekly split.",
                    "Savings goal|files|2026-09-25|Saving for university deposit."]),
            _theme(["college", "university", "application", "admission", "essay", "applying"],
                   [("project", "University applications"), ("topic", "personal statement"), ("person", "Counsellor Ms Boyd")],
                   ["Personal statement draft 3|files|2026-10-06|650 words on debate and community.",
                    "Application tracker|files|2026-09-29|Seven schools, deadlines in January.",
                    "Counsellor meeting|calendar|2026-10-09|Review essays and reference letters.",
                    "Reference letter request|gmail|2026-09-14|Asked Mr Adeyemi for a letter.",
                    "SAT scores report|gmail|2026-09-05|1480 total, retaking maths.",
                    "Scholarship list|files|2026-09-22|Four scholarships to apply for."]),
            _theme(["school", "course", "class", "homework", "chemistry", "history", "grades", "study"],
                   [("topic", "AP Chemistry"), ("topic", "IB History"), ("person", "Mr Adeyemi")],
                   ["Chemistry lab report|files|2026-10-02|Titration lab, error analysis section missing.",
                    "History essay outline|files|2026-09-26|Causes of the First World War.",
                    "Unit test study guide|files|2026-10-07|Equilibrium and acids.",
                    "Homework tracker|files|2026-09-30|Assignments due this week.",
                    "Parent teacher interviews|calendar|2026-10-21|Interviews with Mr Adeyemi.",
                    "Exam timetable|files|2026-10-08|Midterms in the last week of October."]),
            _theme(["volunteer", "shelter", "animals", "service", "community"],
                   [("project", "Animal shelter volunteering"), ("person", "Coordinator Beth")],
                   ["Shelter volunteer roster|calendar|2026-10-04|Sunday afternoons dog walking.",
                    "Adoption day plan|files|2026-09-27|Setting up the adoption fair.",
                    "Volunteer hours log|files|2026-10-01|58 hours so far this year.",
                    "Beth thank you note|gmail|2026-09-15|Thanks for running the fundraiser.",
                    "Fundraiser poster|files|2026-09-10|Poster for the bake sale.",
                    "Orientation for new volunteers|calendar|2026-10-12|Help train new volunteers."]),
        ],
    },
    "retiree": {
        "about": "Dolores, 71, retired primary school teacher in Cork. Widowed. Four grandchildren. "
                 "Grows vegetables, runs a book club, volunteers at the parish, writing a family history.",
        "themes": [
            _theme(["garden", "vegetable", "allotment", "plants", "grow"],
                   [("project", "Vegetable garden"), ("topic", "seed planting"), ("person", "Neighbour Tom")],
                   ["Autumn planting plan|files|2026-09-14|Garlic, broad beans and winter lettuce.",
                    "Seed order|gmail|2026-09-02|Heritage seed order arrives next week.",
                    "Compost heap notes|files|2026-08-27|Turn the heap every fortnight.",
                    "Tom lends the tiller|imessage|2026-09-18|Tom brings the tiller on Saturday.",
                    "Harvest log|files|2026-10-03|Potatoes, beans and tomatoes this month.",
                    "Greenhouse repair|gmail|2026-09-21|Glass panes ordered."]),
            _theme(["grandchild", "family", "grandkids", "visit", "children"],
                   [("person", "Ciara"), ("person", "Seamus"), ("topic", "grandchildren")],
                   ["Seamus school play|calendar|2026-10-11|Seamus plays a shepherd.",
                    "Ciara weekend stay|imessage|2026-09-26|Ciara stays Saturday night.",
                    "Birthday cards list|files|2026-09-05|Birthdays for all four grandchildren.",
                    "Sunday dinner plan|files|2026-10-02|Menu for the family Sunday.",
                    "Photos from the beach|files|2026-08-30|Summer photos to print.",
                    "Gift ideas|files|2026-09-16|Books and a chess set."]),
            _theme(["book", "reading", "club", "literature", "novel"],
                   [("project", "Book club"), ("topic", "novels"), ("person", "Maeve")],
                   ["Book club October pick|files|2026-09-29|Discussing a novel set in 1950s Dublin.",
                    "Maeve hosts next month|imessage|2026-10-04|Maeve offers her sitting room.",
                    "Reading notes|files|2026-10-06|Themes of silence and migration.",
                    "Library hold ready|gmail|2026-09-23|Three books ready to collect.",
                    "Discussion questions|files|2026-09-30|Eight questions for the group.",
                    "Book club list for the year|files|2026-08-20|Twelve titles voted on."]),
            _theme(["health", "doctor", "appointment", "medical", "medication", "hospital"],
                   [("topic", "blood pressure"), ("person", "Dr Murphy"), ("topic", "medication")],
                   ["GP appointment Dr Murphy|calendar|2026-10-15|Blood pressure review.",
                    "Medication list|files|2026-09-10|Current prescriptions and doses.",
                    "Physio exercises|files|2026-09-24|Hip exercises twice daily.",
                    "Eye test booking|gmail|2026-09-17|Annual eye test in November.",
                    "Flu vaccine reminder|gmail|2026-10-01|Flu jab at the pharmacy.",
                    "Blood test results|gmail|2026-09-28|Cholesterol within range."]),
            _theme(["parish", "church", "volunteer", "community", "charity", "faith"],
                   [("project", "Parish volunteering"), ("person", "Father Kieran"), ("topic", "food bank")],
                   ["Food bank rota|calendar|2026-10-05|Thursday morning sorting donations.",
                    "Parish newsletter draft|files|2026-09-19|Writes the notices page.",
                    "Father Kieran asks for help|imessage|2026-09-22|Help with the harvest supper.",
                    "Charity coffee morning|calendar|2026-10-10|Cake stall.",
                    "Parish council notes|files|2026-09-12|Roof repair fund.",
                    "Volunteer thank you|gmail|2026-09-01|Thanks from the parish."]),
            _theme(["history", "genealogy", "family history", "writing", "memoir", "ancestors"],
                   [("project", "Family history book"), ("topic", "genealogy"), ("person", "Cousin Bridget")],
                   ["Family tree draft|files|2026-09-08|Five generations back to Clare.",
                    "Chapter 3 draft|files|2026-10-01|Emigration to Boston in 1912.",
                    "Cousin Bridget photographs|imessage|2026-09-15|Scans of old letters.",
                    "Parish records request|gmail|2026-08-25|Baptism records requested.",
                    "Interview with Aunt Nuala|files|2026-09-20|Recorded memories of the farm.",
                    "Publishing options|files|2026-10-05|Print on demand for the family."]),
            _theme(["travel", "trip", "holiday", "pilgrimage", "journey"],
                   [("topic", "Camino trip"), ("person", "Friend Eileen")],
                   ["Camino route plan|files|2026-09-03|Fifteen days from Sarria.",
                    "Flights with Eileen|gmail|2026-09-13|Return flights to Santiago.",
                    "Packing list|files|2026-09-27|Light layers and walking poles.",
                    "Hostel bookings|gmail|2026-10-02|Albergues booked for the first week.",
                    "Training walks log|files|2026-10-06|Ten km walks three times a week.",
                    "Travel insurance|gmail|2026-09-30|Cover for seniors."]),
        ],
    },
}


def corpus(name: str) -> tuple[list[NoteRec], list[EntityRec], str, list[list[str]], list[set[str]]]:
    """(notes, entities, about card, rubric per theme, entity paths per theme)."""
    p = PERSONAS[name]
    notes: list[NoteRec] = []
    ents: dict[str, EntityRec] = {}
    rubric, theme_paths = [], []
    n = 0
    for t in p["themes"]:
        paths = []
        for kind, title in t["ents"]:
            path = f"{FOLDER[kind]}/{slugify(title)}"
            ents.setdefault(path, EntityRec(path, kind, title, set()))
            paths.append(path)
        theme_paths.append(set(paths))
        rubric.append(t["expect"])
        for k, line in enumerate(t["notes"]):
            title, source, date, summary = line.split("|")
            n += 1
            iid = f"it_{name[:3]}{n:012d}"
            mine = [paths[k % len(paths)], paths[(k + 1) % len(paths)]]
            notes.append(NoteRec(iid, title, source, date, summary, list(dict.fromkeys(mine))))
            for path in mine:
                e = ents[path]
                e.item_ids.add(iid)
                e.last = max(e.last, date)
    return notes, list(ents.values()), p["about"], rubric, theme_paths


def rubric_hits(areas, rubric: list[list[str]]) -> list[bool]:
    text = [f"{a.name} {a.description}".lower() for a in areas]
    return [any(any(re.search(re.escape(w.lower()), t) for w in words) for t in text) for words in rubric]


def theme_purity(areas, theme_paths: list[set[str]]) -> list[float]:
    """For each hidden theme: the largest share of its entities that sits in one area."""
    out = []
    for paths in theme_paths:
        best = max((len(paths & set(a.members)) for a in areas), default=0)
        out.append(best / len(paths))
    return out
