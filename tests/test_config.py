from pathlib import Path
import unittest
import yaml


ROOT = Path(__file__).resolve().parents[1]


class ConfigTest(unittest.TestCase):
    def test_sources_valid(self):
        cfg = yaml.safe_load(
            (ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
        )
        sources = cfg["sources"]
        ids = [s["id"] for s in sources]
        self.assertEqual(len(ids), len(set(ids)))

        for source in sources:
            self.assertIn(source["source_level"], {"S", "A", "B"})
            self.assertIn(source["method"], {"html_index", "rss"})
            self.assertTrue(source["url"].startswith(("http://", "https://")))
            self.assertIn(source["category"], {"market", "policy", "competitor", "industry_news"})

    def test_calendar_dates(self):
        cfg = yaml.safe_load(
            (ROOT / "config" / "calendar.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("events", cfg)
        for event in cfg["events"]:
            self.assertRegex(str(event["date"]), r"^\d{4}-\d{2}-\d{2}$")

    def test_domestic_competitor_sales_sources_present(self):
        cfg = yaml.safe_load(
            (ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
        )
        ids = {s["id"] for s in cfg["sources"]}
        required = {
            "li_auto_ir",
            "xpeng_ir",
            "byd_sales_announcements",
            "geely_auto_press",
            "seres_sales_report",
            "avatr_newscenter",
            "zeekr_group_news",
            "hima_official",
        }
        self.assertTrue(required.issubset(ids))



if __name__ == "__main__":
    unittest.main()
