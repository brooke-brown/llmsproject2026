"""Tests for US-25 (#89): read a summary without the noise.

Evidence links get short labels, and a claim's requirements collapse behind a count.
"""

from core.models import Repo, Requirement

from .test_summary_page import SummaryPageFixture, make_artifact, make_claim

SHA = "abc1234def5678abc1234def5678abc1234def56"


class EvidenceLabelTests(SummaryPageFixture):
    def cite(self, artifact):
        make_claim(self.summary, "Cited work", [artifact])
        return self.get_summary_page()

    def make_review(self, url, state="APPROVED"):
        review = make_artifact(self.repo, "review", "2291840", url)
        review.review_state = state
        review.save()
        return review

    def test_a_pr_is_labeled_by_its_number(self):
        artifact = make_artifact(
            self.repo, "pr", "45", "https://github.com/sample-org/app/pull/45"
        )

        self.assertContains(
            self.cite(artifact),
            '<a href="https://github.com/sample-org/app/pull/45" title="sample-org/app:pr:45">'
            "PR 45</a>",
            html=True,
        )

    def test_an_issue_is_labeled_by_its_number(self):
        artifact = make_artifact(
            self.repo, "issue", "33", "https://github.com/sample-org/app/issues/33"
        )

        self.assertContains(
            self.cite(artifact),
            '<a href="https://github.com/sample-org/app/issues/33" '
            'title="sample-org/app:issue:33">Issue 33</a>',
            html=True,
        )

    def test_a_commit_is_labeled_by_the_first_seven_characters_of_its_sha(self):
        artifact = make_artifact(
            self.repo, "commit", SHA, f"https://github.com/sample-org/app/commit/{SHA}"
        )

        self.assertContains(
            self.cite(artifact),
            f'<a href="https://github.com/sample-org/app/commit/{SHA}" '
            f'title="sample-org/app:commit:{SHA}">Commit abc1234</a>',
            html=True,
        )

    def test_a_review_is_labeled_by_the_pr_in_its_url_not_its_own_id(self):
        url = "https://github.com/sample-org/app/pull/45#pullrequestreview-2291840"

        self.assertContains(
            self.cite(self.make_review(url)),
            f'<a href="{url}" title="sample-org/app:review:2291840">Review on PR 45</a>',
            html=True,
        )

    def test_a_review_whose_url_has_no_pr_number_is_labeled_review(self):
        url = "https://github.com/sample-org/app/reviews/2291840"

        self.assertContains(
            self.cite(self.make_review(url)),
            f'<a href="{url}" title="sample-org/app:review:2291840">Review</a>',
            html=True,
        )

    def test_a_review_still_shows_its_state_after_the_link(self):
        url = "https://github.com/sample-org/app/pull/45#pullrequestreview-2291840"

        response = self.cite(self.make_review(url))

        self.assertContains(response, "Review on PR 45</a> (Approved)")

    def test_a_multi_word_review_state_only_capitalizes_its_first_word(self):
        url = "https://github.com/sample-org/app/pull/45#pullrequestreview-2291840"

        response = self.cite(self.make_review(url, state="CHANGES_REQUESTED"))

        self.assertContains(response, "(Changes requested)")

    def test_the_full_artifact_id_is_no_longer_the_link_text(self):
        response = self.get_summary_page()

        self.assertNotContains(response, ">sample-org/app:pr:12<")

    def test_hovering_tells_apart_same_numbered_items_from_different_repos(self):
        other_repo = Repo.objects.create(
            team=self.team, url="https://github.com/sample-org/api", full_name="sample-org/api"
        )
        other_pr = make_artifact(
            other_repo, "pr", "12", "https://github.com/sample-org/api/pull/12"
        )

        response = self.cite(other_pr)

        self.assertContains(
            response,
            '<a href="https://github.com/sample-org/app/pull/12" title="sample-org/app:pr:12">'
            "PR 12</a>",
            html=True,
        )
        self.assertContains(
            response,
            '<a href="https://github.com/sample-org/api/pull/12" title="sample-org/api:pr:12">'
            "PR 12</a>",
            html=True,
        )


class CollapsedRequirementTests(SummaryPageFixture):
    def tie_fix_claim_to(self, *numbers):
        self.fix_claim.requirement_ids = list(numbers)
        self.fix_claim.save()

    def test_one_requirement_shows_a_singular_count_in_a_native_summary(self):
        # <summary> is what makes the count keyboard-operable and announced by screen readers.
        response = self.get_summary_page()

        self.assertContains(response, "<summary>1 requirement</summary>", html=True)

    def test_several_requirements_show_a_plural_count(self):
        for number in (3, 4):
            Requirement.objects.create(sprint=self.sprint, number=number, text=f"Rule {number}")
        self.tie_fix_claim_to(1, 2, 3, 4)

        response = self.get_summary_page()

        self.assertContains(response, "<summary>4 requirements</summary>", html=True)

    def test_each_claim_expands_on_its_own_and_starts_collapsed(self):
        # A bare tag has no `open`, which would start it expanded, and no `name`, which
        # would close one claim's requirements when another's are opened.
        self.tie_fix_claim_to(2)

        response = self.get_summary_page()

        self.assertContains(response, "<details>", count=2)
        self.assertContains(response, "<details", count=2)

    def test_each_row_has_the_requirements_number_and_text(self):
        response = self.get_summary_page()

        self.assertContains(response, "<li>Requirement 1: Users can log in</li>", html=True)

    def test_rows_are_sorted_by_requirement_number(self):
        self.tie_fix_claim_to(2, 1)

        html = self.get_summary_page().content.decode()

        panel = html[html.index("Fixed the logout redirect") :]
        self.assertLess(
            panel.index("Requirement 1: Users can log in"),
            panel.index("Requirement 2: Users can reset a password"),
        )

    def test_a_repeated_requirement_is_counted_and_listed_once(self):
        self.tie_fix_claim_to(2, 2)

        response = self.get_summary_page()

        self.assertContains(response, "Requirement 2: Users can reset a password", count=1)
        self.assertContains(response, "<summary>1 requirement</summary>", count=2, html=True)

    def test_a_requirement_with_no_match_shows_its_number_and_no_text(self):
        self.tie_fix_claim_to(7)

        response = self.get_summary_page()

        self.assertContains(response, "<li>Requirement 7</li>", html=True)

    def test_a_requirement_with_no_match_still_counts(self):
        self.tie_fix_claim_to(1, 7)

        response = self.get_summary_page()

        self.assertContains(response, "<summary>2 requirements</summary>", html=True)

    def test_a_claim_tied_to_nothing_says_so_and_has_nothing_to_expand(self):
        self.login_claim.requirement_ids = []
        self.login_claim.save()

        response = self.get_summary_page()

        self.assertContains(response, "Not tied to a requirement", count=2)
        self.assertNotContains(response, "<details")

    def test_the_count_stays_under_the_llm_judgment_heading(self):
        html = self.get_summary_page().content.decode()

        heading = html.index("<h3>LLM judgment</h3>")
        panel = html[heading : html.index("</article>", heading)]
        self.assertIn("<summary>1 requirement</summary>", panel)
