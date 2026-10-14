# Direction Startup: Studii

> **Doomscroll your way to an A.** Studii takes the endless vertical feed people already can't put down and fills it with *their own* study material: notes, textbook chapters, lecture slides and books, turned into bite-sized cards, quizzes and recall prompts.

---

## 1. Solves one problem extremely well

**The problem:** Students know they should study, but reach for their phone and scroll social media instead. Traditional study tools (flashcard apps, PDFs, textbooks) get neglected due to the endless feed of content because they take effort to *start*.

**Studii's one job:** make studying *as effortless as scrolling*. The user uploads material once, then opens the app and swipes. Every swipe is a short, active-recall moment drawn from what they actually need to learn.

The app is deliberately **not** trying to be a note-taking app, a tutoring chatbot, a calendar planner or a social network. Every feature serves one loop: **upload → generate → scroll → recall → repeat**.

---

## 2. Knowledge of its customers + access to them

- **Who:** High school and college students preparing for exams, plus self-learners working through books. These are people who already spend hours a day on short-form video feeds.
- **Why we know them:** We *are* the customer. Our group is a group of college students who have lived this exact problem: a textbook open on the desk with a phone in hand.
- **Access:** We have direct, daily access to users through our classes, campus clubs, study groups, dorms, and class group chats. We can recruit beta testers before an exam week, watch them use the app in person, and get same-day feedback.

---

## 3. Familiarity with (or able to gain familiarity with) required infrastructure

Hardware-wise, this app only requires a smart phone to run, which everyone in our group is equipped with and able to use. Our group is also comfortable and has experience in full stack mobile development, which is the primary work going into this project.

---

## 4. Able to reimagine how it can be much better

**What Studii does today:** The existing Studii app already turns studying into a doomscroll-style feed, but the content is **built into the app**. Users scroll through pre-made educational content on a fixed set of topics chosen by the app.
 
**The gap:** Students can't bring in *their own* class material. There is no way to upload a course's lecture slides, homework, readings or notes and study them in the feed. If your exam covers your professor's specific slides, the built-in topics only help as far as they happen to overlap.
 
**How we make it much better:** We add a **personalized feed built from the student's own course content**:
 
- **Upload your class material:** Lecture slides, homework sets, PDFs, readings or photos of handwritten notes.
- **Generate personalized active-recall content:** An LLM turns the uploaded material into swipeable cards (recall questions, multiple-choice quizzes and fill-in-the-blank prompts), all grounded in that specific course.
- **Study exactly what's on your exam:** The feed covers your professor's terminology, examples and emphasis instead of a generic version of the subject.
- **Adaptive review:** Cards you miss come back sooner (spaced repetition), and cards you've mastered fade out, so the feed focuses on your weak spots in *your* class.
- **Keeps what already works:** The addictive scroll format that makes Studii engaging stays the same. We change what fills the feed, from generic built-in topics to content made from your own course.

---

## 5. LLM / Generative AI is essential (not a sidebar chatbot)

The LLM **is** the product engine. Without it, Studii is an empty feed.

- **Content generation:** The LLM reads the user's uploaded material and produces every card in the feed: recall questions, multiple-choice quizzes, cloze (fill-in-the-blank) prompts, "explain it simply" snippets and chapter checkpoints.
- **Chunking and topic structure:** The LLM segments long documents into topics and concepts, which drive the feed order and the spaced-repetition schedule.
- **Grading free-text answers:** When a user types a short answer, the LLM judges it against the source passage and gives a one-line correction.
- **Adaptive regeneration:** When a user keeps missing a concept, the LLM generates new cards that approach it from a different angle.

There is no "Ask AI" chat box tacked on the side. Remove the LLM and no feed can exist.

---

## 6. No LLM-generated artifact reaches the user without passing through a validator

Every generated card goes through a **validation pipeline** before it is saved to the user's feed. Cards are only marked `published` after all checks pass.

**Validator checks:**

1. **Schema check:** The output must match a strict JSON schema (card type, question, answer, options, source reference). Malformed output is rejected.
2. **Grounding check:** Every card must cite the source chunk it came from, and its answer must be supported by that chunk (the cited quote must actually appear in the source text, plus an entailment check). This prevents hallucinated "facts" from entering a student's study material.
3. **Quiz integrity:**
   - Exactly one correct option for multiple choice
   - The correct answer appears among the options
   - No duplicate options
   - The answer is not given away in the question text
4. **Length / readability:** Cards must fit on one phone screen (character limits per field) and stay within a target reading level.
5. **Duplicate detection:** Embedding similarity against existing cards in the same deck rejects near-duplicates.
6. **Safety / appropriateness:** Content filter for anything inappropriate or off-topic relative to the source.

The same applies to LLM grading of free-text answers. The verdict must match a schema (`correct | partially_correct | incorrect` plus a short justification grounded in the source) before it is shown.

---

## 7. Validation errors automatically feed back into the LLM to self-correct

When a card fails validation, Studii does **not** silently drop it or show it anyway. It runs a **repair loop**:

```
generate card
   │
   ▼
validate ──pass──► publish to feed
   │
  fail
   │
   ▼
build repair prompt:
  - original source chunk
  - the failed card
  - specific validator errors, e.g.
    "Option C duplicates option A"
    "Answer not supported by source chunk 14"
    "Question exceeds 180 characters"
   │
   ▼
LLM regenerates the card ──► validate again
   │
(up to N = 3 attempts)
   │
still failing ──► discard card + log for review
```

- Errors are passed back as **structured, specific messages**, not just "try again", so the model knows exactly what to fix.
- Failure rates per check are logged. That lets us tune prompts over time, for example if grounding failures spike on a certain document type.

---

## 8. Deployable end-to-end (more than an MVP)

If built, Studii ships as a working, deployed product:

- **Accounts:** sign up, log in, personal library (Supabase Auth)
- **Upload:** PDF, slides, photos of notes and EPUB files, stored in the cloud
- **Ingestion pipeline:** extract → chunk → embed → store, run as a background job with progress status
- **Generation + validation + repair loop:** produces validated cards per deck
- **The feed:** infinite vertical scroll of cards with swipe and tap interactions
- **Progress tracking:** spaced-repetition scheduling, mastery per topic, streaks
- **Hosting:** web/PWA on Vercel, mobile build via Expo, backend on Supabase, with environment config and monitoring/logging

**Planned but possibly not in v1:** exam-countdown mode, shared class decks, audio/"podcast" cards, and a social study-streaks leaderboard. The core loop is fully end-to-end without them.
