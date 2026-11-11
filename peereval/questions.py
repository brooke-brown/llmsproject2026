"""The questions every peer evaluation asks, each answered from 1 to 5.

Kept in code so the beta needs no question editor. Ratings store scores by `key`, so a
question's wording can change later without changing old answers; never reuse or rename a key.
"""

from typing import NamedTuple


class Question(NamedTuple):
    key: str
    text: str
    label_1: str
    label_5: str


QUESTIONS = (
    Question("communication", "Communicates with the team", "rarely", "consistently and clearly"),
    Question("on_time", "Gets their work done on time", "rarely", "always"),
    Question("effort", "Puts effort into the project", "very little", "a great deal"),
    Question("quality", "Does work the team can build on", "rarely", "consistently"),
    Question("cooperation", "Works well with teammates", "rarely", "consistently"),
)

QUESTION_KEYS = frozenset(question.key for question in QUESTIONS)
MIN_SCORE = 1
MAX_SCORE = 5
