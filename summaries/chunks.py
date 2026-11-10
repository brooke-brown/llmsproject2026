"""How a covered range is split into chunks for the LLM (T-20, #120).

Plain dates in, plain dates out. The rule looks only at the first and last day, never at
the student, so every student on a team is chunked the same way and their scores stay
comparable.
"""

from datetime import date, timedelta

CHUNK_DAYS = 7
# A leftover this short joins the previous chunk instead of being its own, so a range of up
# to CHUNK_DAYS + MAX_LEFTOVER_DAYS days is one chunk.
MAX_LEFTOVER_DAYS = 3


def split_range(first_day: date, last_day: date) -> list[tuple[date, date]]:
    """Consecutive (first, last) chunks that cover first_day through last_day, inclusive.

    Chunks are CHUNK_DAYS long from the first day. A leftover of MAX_LEFTOVER_DAYS or fewer
    days joins the previous chunk. Raises ValueError if the range ends before it starts.
    """
    if last_day < first_day:
        raise ValueError(f"The range ends on {last_day}, before it starts on {first_day}.")
    total = (last_day - first_day).days + 1
    count, leftover = divmod(total, CHUNK_DAYS)
    if leftover and (leftover > MAX_LEFTOVER_DAYS or count == 0):
        count += 1
    chunks = []
    start = first_day
    for index in range(count):
        end = last_day if index == count - 1 else start + timedelta(days=CHUNK_DAYS - 1)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks
