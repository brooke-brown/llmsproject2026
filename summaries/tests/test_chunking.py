"""Tests for T-20 (#120): splitting a covered range into chunks (plain dates, no database)."""

from datetime import date, timedelta
from unittest import TestCase


def split_range(*args, **kwargs):
    from summaries.chunks import split_range

    return split_range(*args, **kwargs)


FIRST = date(2026, 10, 23)


def days(count):
    """The chunks for a range of `count` days from FIRST, as lengths."""
    chunks = split_range(FIRST, FIRST + timedelta(days=count - 1))
    return [(last - first).days + 1 for first, last in chunks]


class SplitRangeTests(TestCase):
    def test_the_constants_are_named(self):
        from summaries import chunks

        self.assertEqual(chunks.CHUNK_DAYS, 7)
        self.assertEqual(chunks.MAX_LEFTOVER_DAYS, 3)

    def test_up_to_ten_days_is_one_chunk(self):
        for count in (1, 6, 7, 8, 9, 10):
            self.assertEqual(days(count), [count], count)

    def test_eleven_days_is_a_week_and_a_short_chunk(self):
        self.assertEqual(days(11), [7, 4])

    def test_whole_weeks_split_evenly(self):
        self.assertEqual(days(14), [7, 7])
        self.assertEqual(days(21), [7, 7, 7])

    def test_a_small_leftover_joins_the_previous_chunk(self):
        self.assertEqual(days(15), [7, 8])
        self.assertEqual(days(17), [7, 10])

    def test_a_leftover_of_four_or_more_stays_its_own_chunk(self):
        self.assertEqual(days(18), [7, 7, 4])

    def test_chunks_are_consecutive_and_cover_the_range_exactly(self):
        last = FIRST + timedelta(days=27)
        chunks = split_range(FIRST, last)
        self.assertEqual(chunks[0][0], FIRST)
        self.assertEqual(chunks[-1][1], last)
        for (_, end), (start, _) in zip(chunks, chunks[1:]):
            self.assertEqual(start, end + timedelta(days=1))

    def test_the_rule_depends_only_on_the_dates(self):
        self.assertEqual(split_range(FIRST, date(2026, 11, 10)), split_range(FIRST, date(2026, 11, 10)))

    def test_a_range_that_ends_before_it_starts_raises(self):
        with self.assertRaises(ValueError):
            split_range(FIRST, FIRST - timedelta(days=1))
