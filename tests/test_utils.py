import unittest

from src.utils import (
    canonical_url,
    period_markers,
    same_event_title,
    title_similarity,
)


class UtilsTest(unittest.TestCase):
    def test_canonical_url_removes_tracking_but_keeps_id(self):
        url = "https://example.com/a?id=123&utm_source=x&from=share"
        self.assertEqual(
            canonical_url(url),
            "https://example.com/a?id=123",
        )

    def test_different_query_id_not_collapsed(self):
        a = canonical_url("https://example.com/article?id=123")
        b = canonical_url("https://example.com/article?id=456")
        self.assertNotEqual(a, b)

    def test_similar_event_titles_are_duplicates(self):
        a = "宝马中国二季度销量下滑，纯电车型承压"
        b = "宝马中国Q2销量下滑：纯电车型继续承压"
        self.assertGreaterEqual(title_similarity(a, b), 0.70)
        self.assertTrue(same_event_title(a, b))

    def test_daily_bulletin_different_dates_not_duplicate(self):
        a = "财联社汽车早报 8月12日"
        b = "财联社汽车早报 8月13日"
        self.assertNotEqual(period_markers(a), period_markers(b))
        self.assertFalse(same_event_title(a, b))

    def test_monthly_sales_different_month_not_duplicate(self):
        a = "6月新能源汽车销量同比增长20%"
        b = "7月新能源汽车销量同比增长20%"
        self.assertFalse(same_event_title(a, b))


if __name__ == "__main__":
    unittest.main()
