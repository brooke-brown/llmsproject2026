# Startup: Studii (Doomscrolling for Studying)

Studii is an app that turns studying into a doomscroll-style feed of short educational content. Its content is built into the app and covers a fixed set of topics, so students can't study their own class material in the feed.

## Problem It Solves Extremely Well
Students reach for their phone and scroll instead of studying, because study tools take effort to *start*. Studii makes studying as effortless as scrolling: open the app, swipe, and every swipe is a quick active-recall moment.

## Access to Target Customers
We *are* the target customer: college students who study with a textbook open and a phone in hand. We have direct access to hundreds of peers through classes, dorms, clubs, study groups, and UVA CS organizations for testing, feedback, and early distribution, especially right before exam weeks.

## Familiarity with Required Infrastructure
The app only requires a smartphone, which every team member has. All team members have experience with full-stack mobile development, which is the primary work in this project.

## How We Reimagine the Problem Space
Studii's feed only offers pre-made content on topics the app chooses. It can't help with *your* professor's slides or *your* homework. We let students upload their own lecture slides, homework, readings, and notes, and generate a personalized feed of active-recall cards (recall questions, multiple-choice quizzes, fill-in-the-blanks) from that material. Cards you miss come back sooner (spaced repetition), so the feed focuses on your weak spots in your actual class.

## LLM as Core to the Solution
The LLM reads the uploaded course material, breaks it into topics, and generates every card in the personalized feed. It also grades short typed answers against the source material and creates new cards from a different angle when a student keeps missing a concept. Without the LLM, there is no personalized feed.

## Validation / Guardrails (candidates)
- **Grounding check:** every card's answer must trace to a specific passage in the uploaded material; cards that can't be traced are rejected.
- **Schema check:** each card must match a strict format (card type, question, answer, options, source reference).
- **Quiz integrity check:** exactly one correct option, the correct answer appears among the options, no duplicate options, and the question doesn't give the answer away.
- **Length check:** each card must fit on one phone screen.

## Self-Correction Loop
The validator returns a structured list of specific errors (e.g., "answer not supported by source passage," "option C duplicates option A"). That list is fed back into the LLM prompt along with the original passage for an automatic retry, capped at N attempts. Cards that still fail are discarded and logged so prompts can be improved.

## Deployability
Yes, fully deployable within the class timeline: mobile app + backend (accounts, uploads, card generation, the feed, and progress tracking) can run on free/student tiers.
