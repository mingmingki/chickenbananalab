from django.test import SimpleTestCase

from . import naver_news


class OfficialNewsFeedsTests(SimpleTestCase):
    """Only feeds the production server can read: the others logged an error on every refresh."""

    def test_no_feed_that_refuses_the_server(self):
        urls = [url for feeds in naver_news._CBL_GLOBAL_OFFICIAL_FEEDS_V28.values() for _, url in feeds]
        self.assertTrue(urls)
        for blocked in ("autodesk.com/blogs/aec", "cisa.gov"):
            self.assertFalse([url for url in urls if blocked in url], blocked)
