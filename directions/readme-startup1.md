# Startup: IVY (Period Tracking in Your Own Words)

IVY by Bellabeat is a period and cycle tracker where users log period days, flow, symptoms, and moods, and the app predicts their next period and fertile window. Logging means tapping through checklists every day, and the data is stored but rarely explained, so users get a calendar instead of answers about their own body.

## Problem It Solves Extremely Well
People start tracking their cycle, then stop after a few weeks because logging is tedious and the payoff is just a predicted date that's often wrong. IVY makes tracking as effortless as texting a friend: type one sentence about how you feel, and the app logs it and shows you what's actually changing over time.

## Access to Target Customers
We know the target customer well: college students who have tried cycle-tracking apps and abandoned them. We have direct access to hundreds of peers through classes, dorms, clubs, women's health and pre-med organizations, and UVA CS organizations for testing over multiple cycles, feedback, and early distribution.

## Familiarity with Required Infrastructure
The app only requires a smartphone, which every team member has. All team members have experience with full-stack mobile development, which is the primary work in this project. The optional Ivy bracelet and phone health data (Apple Health, Android Health Connect) connect through standard mobile APIs we can learn quickly.

## How We Reimagine the Problem Space
IVY's checklists make logging a chore, its predictions are weak for anyone with an irregular cycle, and months of entries never turn into insight. We let users log in plain language ("bad cramps since lunch, bloated, slept badly") and turn that into structured entries they confirm with one tap. From their own data, the app generates personal insights ("severe cramps on 3 days this cycle, up from 1 on average"), shows predictions as an honest range, and creates a one-tap summary of the last few months to bring to a doctor's appointment.

## LLM as Core to the Solution
The LLM turns free-text or voice entries into structured logs, explains patterns in the user's own data in plain language, and writes the doctor-visit summary. When an entry is vague ("felt off today"), it asks one short follow-up question instead of a checklist. Without the LLM, IVY is back to checkboxes and a calendar.

## Validation / Guardrails (candidates)
- **Grounding check:** every number and claim in an insight or summary must match the user's actual logs (the validator recomputes the figures), and every logged symptom must trace to the user's original words.
- **Schema check:** each log must match a strict format (symptom from the allowed list, severity, date, source text).
- **Medical safety check:** no diagnoses, no treatment advice, no "you can't get pregnant today" claims; red-flag symptoms (very heavy bleeding, severe pain, fainting) always show a fixed "talk to a healthcare provider" message.
- **Length check:** each insight must fit on one phone screen.

## Self-Correction Loop
The validator returns a structured list of specific errors (e.g., "claim says 4 severe days, data shows 3," "'nausea' not in user's entry"). That list is fed back into the LLM prompt along with the user's original entry or data for an automatic retry, capped at N attempts. Outputs that still fail fall back to plain stats or the manual log form and are logged so prompts can be improved. Safety failures are never rewritten into softer advice; they always resolve to the fixed provider message.

## Deployability
Yes, fully deployable within the class timeline: mobile app + backend (accounts, encrypted logs, natural-language logging, insights, and doctor summaries) can run on free/student tiers.
