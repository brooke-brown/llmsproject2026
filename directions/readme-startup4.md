# Startup: Fabric (Personal Location-Based Journal)

Fabric (YC S16, founded by ex-Facebook engineers) automatically built a private timeline of a user's life by pulling in location history, photos, and social posts onto a map. It shut down, but the core idea — effortless personal journaling — remains unsolved well today.

## Problem It Solves Extremely Well
Eliminates the friction of manual journaling by passively capturing *where you were, when, and with what photos*, so a timeline of your life exists without ever writing a word.

## Access to Target Customers
We *are* the target customer: undergrads who want a record of college life/travel but never journal manually. We have direct access to hundreds of peers through dorms, clubs, and UVA CS organizations for testing, feedback, and early distribution.

## Familiarity with Required Infrastructure
All team-members have taken dedicated courses which cover concepts related to mobile-app development.

## How We Reimagine the Problem Space
Fabric only auto-logged raw data points on a map — it never turned them into something readable. We generate an actual narrated journal: synthesizing GPS traces, photo content, and calendar/time signals into first-person story entries, weekly recaps, and a natural-language "ask your memories" search (e.g., "what did I do last time I was in DC?").

## LLM as Core to the Solution
The LLM's job is to ingest structured multimodal signals (location clusters, photo captions from a vision model, timestamps, movement patterns) and produce a coherent personal narrative entry. A second LLM-powered layer lets users query their own journal in natural language.

## Validation / Guardrails (candidates)
- **Fact-grounding check:** every sentence in a generated entry must trace to a source fact (GPS point, timestamp, photo metadata); untraceable sentences are stripped.

## Self-Correction Loop
The validator returns a structured list of unsupported/failed claims. That list is fed back into the LLM prompt ("these statements are unsupported by source data — regenerate using only verified facts") for an automatic retry, capped at N attempts, after which a safe template-based entry is used as fallback.

## Deployability
Yes, fully deployable within the class timeline: mobile app + backend can run on free/student tiers.
