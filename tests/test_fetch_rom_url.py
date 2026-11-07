import unittest
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import fetch_rom_url


class FetchRomUrlTests(unittest.TestCase):
    def test_direct_url_pass_through(self):
        url = "https://example.com/rom.zip"
        self.assertEqual(fetch_rom_url.fetch_rom_url(url), url)
        http_url = "http://example.com/base.zip"
        self.assertEqual(fetch_rom_url.fetch_rom_url(http_url), http_url)

    def test_is_direct_url(self):
        self.assertTrue(fetch_rom_url.is_direct_url("https://gauss.allawnfs.com/ota.zip"))
        self.assertTrue(fetch_rom_url.is_direct_url("http://gauss.allawnfs.com/ota.zip"))
        self.assertFalse(fetch_rom_url.is_direct_url("rmx3301/rmx3301"))
        self.assertFalse(fetch_rom_url.is_direct_url("Oneplus 13"))

    def test_score_model_rmx(self):
        score_3300 = fetch_rom_url.score_model(
            "rmx3301/rmx3301", "Realme", "RMX3300", "Realme GT2 Pro"
        )
        score_unrelated = fetch_rom_url.score_model(
            "rmx3301/rmx3301", "OnePlus", "NE2210", "一加 10 Pro"
        )
        self.assertGreater(score_3300, 400)
        self.assertGreater(score_3300, score_unrelated)

    def test_score_model_oneplus_13(self):
        score_13 = fetch_rom_url.score_model(
            "Oneplus 13", "oneplus-global", "CPH2649", "OnePlus 13"
        )
        score_13cn = fetch_rom_url.score_model(
            "Oneplus 13", "OnePlus", "PJZ110", "一加 13"
        )
        score_10 = fetch_rom_url.score_model(
            "Oneplus 13", "OnePlus", "NE2210", "一加 10 Pro"
        )
        self.assertGreater(score_13, 300)
        self.assertGreater(score_13cn, 300)
        self.assertGreater(score_13, score_10)

    @patch("fetch_rom_url.get_all_brand_models")
    @patch("fetch_rom_url.get_version_page_urls")
    @patch("fetch_rom_url.resolve_rom_download_link_via_playwright")
    def test_fetch_rom_url_resolution(
        self, mock_resolve, mock_versions, mock_models
    ):
        mock_models.return_value = [
            ("Realme", "RMX3300", "Realme GT2 Pro"),
            ("OnePlus", "PJZ110", "一加 13"),
        ]
        mock_versions.return_value = [
            "https://roms.lian86.top/brand/Realme/model/RMX3300/version/Realme%20UI%20RMX3300_15.0.0.1410(CN01)"
        ]
        mock_resolve.return_value = "https://gauss.allawnfs.com/ota_rmx3300.zip"

        result = fetch_rom_url.fetch_rom_url("rmx3301/rmx3301")
        self.assertEqual(result, "https://gauss.allawnfs.com/ota_rmx3300.zip")
        mock_versions.assert_called_once_with("Realme", "RMX3300")
        mock_resolve.assert_called_once_with(
            "https://roms.lian86.top/brand/Realme/model/RMX3300/version/Realme%20UI%20RMX3300_15.0.0.1410(CN01)"
        )

    @patch("fetch_rom_url.get_all_brand_models")
    def test_fetch_rom_url_not_found(self, mock_models):
        mock_models.return_value = []
        with self.assertRaises(ValueError):
            fetch_rom_url.fetch_rom_url("nonexistent_device_xyz")


if __name__ == "__main__":
    unittest.main()
