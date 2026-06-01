import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services import tides


class TideServiceTest(unittest.TestCase):
    def test_circular_quay_override_for_may_22(self):
        result = tides.get_tides_for_date(datetime(2026, 5, 22))
        self.assertEqual(
            [(x["time"].strftime("%H:%M"), x["height_m"], x["is_high"]) for x in result],
            [
                ("00:18", 1.78, True),
                ("07:17", 0.34, False),
                ("13:22", 1.25, True),
                ("18:48", 0.63, False),
            ],
        )
        self.assertEqual(result[0]["source"], "circular_quay")

    def test_circular_quay_override_for_june_1(self):
        result = tides.get_tides_for_date(datetime(2026, 6, 1))
        self.assertEqual(
            [(x["time"].strftime("%H:%M"), x["height_m"], x["is_high"]) for x in result],
            [
                ("03:00", 0.53, False),
                ("08:51", 1.35, True),
                ("14:15", 0.69, False),
                ("20:54", 1.84, True),
            ],
        )
        self.assertEqual(result[0]["source"], "circular_quay")

    def test_official_delay_shifts_times_only(self):
        result = tides.get_tides_for_date(datetime(2026, 5, 22), delay_minutes=15)
        self.assertEqual(result[0]["time"].strftime("%H:%M"), "00:33")
        self.assertEqual(result[0]["height_m"], 1.78)

    @patch("services.tides._tidecheck_key", return_value="tc_key")
    @patch("services.tides._tidecheck_station_id", return_value="station-1")
    @patch("services.tides._fetch_tidecheck_extremes")
    def test_tidecheck_is_used_when_available(self, mock_fetch, _mock_station, _mock_key):
        with TemporaryDirectory() as tmpdir, patch.dict("os.environ", {"APP_DATA_DIR": tmpdir}):
            mock_fetch.return_value = [
                {"time": datetime(2026, 5, 22, 0, 18), "is_high": True, "label": "🟢 满潮", "height_m": 1.78, "source": "tidecheck"},
                {"time": datetime(2026, 5, 22, 7, 17), "is_high": False, "label": "🔵 干潮", "height_m": 0.34, "source": "tidecheck"},
            ]

            result = tides.get_tides_for_date(datetime(2026, 5, 22))

        self.assertEqual(result[0]["source"], "tidecheck")
        self.assertEqual(result[0]["time"].strftime("%H:%M"), "00:18")
        mock_fetch.assert_called_once_with("station-1", "2026-05-22")

    @patch("services.tides._tidecheck_key", return_value="tc_key")
    @patch("services.tides._tidecheck_station_id", return_value="")
    @patch("services.tides._fetch_tidecheck_nearest_station", return_value="nearest-1")
    @patch("services.tides._fetch_tidecheck_extremes")
    def test_tidecheck_can_use_nearest_station(self, mock_fetch, mock_nearest, _mock_station, _mock_key):
        with TemporaryDirectory() as tmpdir, patch.dict("os.environ", {"APP_DATA_DIR": tmpdir}):
            mock_fetch.return_value = [
                {"time": datetime(2026, 5, 22, 13, 22), "is_high": True, "label": "🟢 满潮", "height_m": 1.25, "source": "tidecheck"},
                {"time": datetime(2026, 5, 22, 18, 48), "is_high": False, "label": "🔵 干潮", "height_m": 0.63, "source": "tidecheck"},
            ]

            result = tides.get_tides_for_date(datetime(2026, 5, 22), delay_minutes=10)

        self.assertEqual(result[0]["time"].strftime("%H:%M"), "13:32")
        self.assertEqual(result[0]["height_m"], 1.25)
        mock_nearest.assert_called_once()
        mock_fetch.assert_called_once_with("nearest-1", "2026-05-22")

    @patch("services.tides._tidecheck_key", return_value="tc_key")
    @patch("services.tides._fetch_tidecheck_extremes")
    def test_tidecheck_persistent_cache_reuses_api_response(self, mock_fetch, _mock_key):
        with TemporaryDirectory() as tmpdir, patch.dict("os.environ", {"APP_DATA_DIR": tmpdir}):
            mock_fetch.return_value = [
                {"time": datetime(2026, 6, 4, 1, 20), "is_high": True, "label": "🟢 满潮", "height_m": 1.7, "source": "tidecheck"},
                {"time": datetime(2026, 6, 4, 8, 10), "is_high": False, "label": "🔵 干潮", "height_m": 0.4, "source": "tidecheck"},
            ]

            first = tides._fetch_tidecheck_extremes_cached("station-1", "2026-06-04")
            second = tides._fetch_tidecheck_extremes_cached("station-1", "2026-06-04")

        self.assertEqual(first, second)
        mock_fetch.assert_called_once_with("station-1", "2026-06-04")

    def test_estimate_returns_target_day_events(self):
        result = tides.get_tides_for_date(datetime(2026, 5, 20), delay_minutes=10)
        self.assertGreaterEqual(len(result), 1)
        self.assertTrue(all(x["time"].date() == datetime(2026, 5, 20).date() for x in result))
        self.assertTrue(all("time" in x and "is_high" in x and "label" in x for x in result))

    def test_tide_source_label_marks_estimate(self):
        result = tides.get_tides_for_date(datetime(2026, 5, 20), delay_minutes=10)
        self.assertIn("估算", tides.get_tide_source_label(result))
        self.assertTrue(tides.is_estimated_tide(result))

    @patch("services.tides._worldtides_key", return_value="abc")
    @patch("services.tides._fetch_worldtides_extremes")
    def test_worldtides_is_used_when_available(self, mock_fetch, _mock_key):
        mock_fetch.return_value = [
            {"time": datetime(2026, 5, 20, 2, 0), "is_high": True, "label": "🟢 满潮"},
            {"time": datetime(2026, 5, 20, 8, 10), "is_high": False, "label": "🔵 干潮"},
            {"time": datetime(2026, 5, 20, 14, 20), "is_high": True, "label": "🟢 满潮"},
            {"time": datetime(2026, 5, 20, 20, 30), "is_high": False, "label": "🔵 干潮"},
        ]
        result = tides.get_tides_for_date(datetime(2026, 5, 20), lat=-33.85, lon=151.2)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["time"].hour, 2)
        mock_fetch.assert_called_once()

    @patch("services.tides._worldtides_key", return_value="abc")
    @patch("services.tides._fetch_worldtides_extremes", side_effect=RuntimeError("boom"))
    def test_falls_back_when_worldtides_fails(self, _mock_fetch, _mock_key):
        result = tides.get_tides_for_date(datetime(2026, 5, 20), delay_minutes=5, lat=-33.85, lon=151.2)
        self.assertGreaterEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
