import re

from django.test import TestCase


class HomePageTitleTests(TestCase):
    def test_home_title_is_plain_text(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        titles = re.findall(r"<title>(.*?)</title>", html, flags=re.S)
        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].strip(), "ChickenBanana Lab | 건설·BIM·IT 자동화")

    def test_patches_that_were_stuck_in_the_title_are_not_rendered(self):
        # They were never applied (inside <title> they were plain text); keep
        # the page as it looks today instead of silently activating them.
        html = self.client.get("/").content.decode("utf-8")
        for marker in (
            "CBL_HOME_4CARDS_ONELINE_TUNE_START",
            "CBL_MAIN_SECOND_ROW_FINAL_ALIGN_START",
            "CBL_CALENDAR_COLOR_ALLDAY_RANGE_STYLE_START",
            "CBL_CALENDAR_REGISTER_COLOR_FIX_STYLE_START",
            "CBL_CALENDAR_CONNECTED_BAR_PREVIEW_FIX_START",
        ):
            self.assertNotIn(marker, html)
