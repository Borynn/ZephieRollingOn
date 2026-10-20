"""游戏分辨率选择、归一化和点击坐标的边界条件。"""
import unittest
from unittest.mock import patch

import numpy as np

from zephie_rolling_on.app.runtime_state import RuntimeState, use_runtime
from zephie_rolling_on.executor.win32_click import _to_physical_space
from zephie_rolling_on.vision.scale_norm import (
    GAME_RESOLUTIONS,
    RESOLUTION_MISMATCH,
    baseline_point,
    capture_scale_state,
    configure_game_resolution,
    normalize_window_frame,
    set_frame_scale_percent,
)


class GameResolutionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = RuntimeState()
        self.context = use_runtime(self.runtime)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def prepare(self, resolution=(1600, 900), percent=125):
        set_frame_scale_percent(percent)
        configure_game_resolution(resolution)

    def test_native_frame_uses_original_pixels_at_high_monitor_scale(self):
        self.prepare()
        frame = np.zeros((900, 1600, 3), np.uint8)
        with patch("zephie_rolling_on.vision.scale_norm.cv2.resize") as resize:
            result = normalize_window_frame(frame, (1600, 900))
        self.assertIs(result, frame)
        resize.assert_not_called()
        self.assertEqual(_to_physical_space(400, 200), (400, 200))

    def test_all_three_resolutions_and_monitor_scales(self):
        for resolution in GAME_RESOLUTIONS:
            for percent in (100, 125, 150, 175, 200):
                with self.subTest(resolution=resolution, percent=percent):
                    self.prepare(resolution, percent)
                    w, h = (round(n * percent / 100) for n in resolution)
                    frame = np.zeros((h, w, 3), np.uint8)
                    result = normalize_window_frame(frame, (w, h))
                    self.assertEqual(result.shape[:2], (resolution[1], resolution[0]))
                    self.assertEqual(_to_physical_space(400, 200), (round(400 * w / resolution[0]), round(200 * h / resolution[1])))
                    self.assertEqual(baseline_point(*_to_physical_space(400, 200)), (400, 200))

    def test_tolerance_is_five_pixels_on_each_axis(self):
        for target in ((1600, 900), (2000, 1125)):
            for dx, dy, valid in ((5, -5, True), (-5, 5, True), (6, 0, False), (0, -6, False)):
                with self.subTest(target=target, dx=dx, dy=dy):
                    self.prepare()
                    w, h = target[0] + dx, target[1] + dy
                    frame = np.zeros((h, w, 3), np.uint8)
                    if valid:
                        self.assertIsNotNone(normalize_window_frame(frame, (w, h)))
                    else:
                        with self.assertRaisesRegex(ValueError, RESOLUTION_MISMATCH):
                            normalize_window_frame(frame, (w, h))

    def test_missing_and_wrong_selection_block_recognition(self):
        self.prepare(None)
        frame = np.zeros((720, 1280, 3), np.uint8)
        with self.assertRaisesRegex(ValueError, "请选择游戏分辨率"):
            normalize_window_frame(frame)
        self.prepare()
        with self.assertRaisesRegex(ValueError, RESOLUTION_MISMATCH):
            normalize_window_frame(frame)
        self.assertIs(capture_scale_state().raw_frame, frame)

    def test_run_keeps_its_chosen_mode_until_prepared_again(self):
        self.prepare()
        normalize_window_frame(np.zeros((900, 1600, 3), np.uint8))
        with self.assertRaisesRegex(ValueError, RESOLUTION_MISMATCH):
            normalize_window_frame(np.zeros((1125, 2000, 3), np.uint8))
        self.prepare()
        self.assertIsNotNone(normalize_window_frame(np.zeros((1125, 2000, 3), np.uint8)))

    def test_instances_keep_separate_resolution_and_mapping(self):
        self.prepare()
        normalize_window_frame(np.zeros((900, 1600, 3), np.uint8), (1600, 900))
        other = RuntimeState()
        with use_runtime(other):
            self.prepare((1920, 1080), 150)
            normalize_window_frame(np.zeros((1620, 2880, 3), np.uint8), (2880, 1620))
            self.assertEqual(_to_physical_space(400, 200), (600, 300))
        self.assertEqual(_to_physical_space(400, 200), (400, 200))
        self.assertEqual(capture_scale_state().resolution, (1600, 900))

    def test_capture_checks_unmodified_frame_and_resizes_once(self):
        from zephie_rolling_on.vision import capture

        self.prepare()
        raw = np.zeros((1125, 2000, 3), np.uint8)
        import cv2

        with (
            patch.object(capture, "_capture_window_client_raw", return_value=raw),
            patch("zephie_rolling_on.vision.win32_window._client_size_screen_pixels", return_value=(0, 0, 2000, 1125)),
            patch("zephie_rolling_on.vision.scale_norm.cv2.resize", wraps=cv2.resize) as resize,
        ):
            result = capture.capture_window_client(1)
        self.assertEqual(result.shape[:2], (900, 1600))
        self.assertEqual(resize.call_count, 1)

    def test_wgc_client_crop_preserves_native_pixels(self):
        from zephie_rolling_on.vision.capture_wgc import _crop_frame_to_client

        raw = np.zeros((900, 1600, 3), np.uint8)
        with (
            patch("zephie_rolling_on.vision.win32_window._client_size_screen_pixels", return_value=(100, 100, 2000, 1125)),
            patch("zephie_rolling_on.vision.capture_wgc._window_rect_for_capture", return_value=(100, 100, 2100, 1225)),
            patch("win32gui.ClientToScreen", return_value=(100, 100)),
            patch("cv2.resize") as resize,
        ):
            result = _crop_frame_to_client(raw, 1)
        self.assertEqual(result.shape, raw.shape)
        self.assertTrue(np.shares_memory(result, raw))
        resize.assert_not_called()

    def test_capture_mismatch_retains_original_for_export(self):
        from zephie_rolling_on.vision import capture
        from zephie_rolling_on.ui.log_export import _capture_images

        self.prepare()
        raw = np.zeros((720, 1280, 3), np.uint8)
        with (
            patch.object(capture, "_capture_window_client_raw", return_value=raw),
            patch("zephie_rolling_on.vision.win32_window._client_size_screen_pixels", return_value=(0, 0, 1280, 720)),
        ):
            self.assertIsNone(capture.capture_window_client(1))
        self.assertIn(RESOLUTION_MISMATCH, capture.last_capture_failure_note())
        with (
            patch("zephie_rolling_on.diagnose.last_captured_frame", return_value=None),
            patch("zephie_rolling_on.diagnose.last_raw_captured_frame", return_value=capture_scale_state().raw_frame),
        ):
            images = _capture_images()
        self.assertEqual([name for name, _image in images], ["截图-原始客户区.png"])
        import cv2

        decoded = cv2.imdecode(np.frombuffer(images[0][1], np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, raw.shape)


if __name__ == "__main__":
    unittest.main()
