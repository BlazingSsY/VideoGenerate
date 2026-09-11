import unittest

from app.catalog import get_model
from app.dashscope import _service_base_url, build_payload, is_insufficient_balance


class ModelMediaCapabilityTests(unittest.TestCase):
    def test_wan_reference_mode_exposes_image_video_and_audio_inputs(self):
        model = get_model("wan3.0-video-prime")
        capability = model.capability("r2v")

        self.assertIsNotNone(capability)
        self.assertEqual(
            [(item.kind, item.media_type, item.max_count) for item in capability.media_inputs],
            [
                ("image", "reference_image", 10),
                ("video", "reference_video", 5),
                ("audio", "reference_audio", 5),
            ],
        )
        self.assertEqual(capability.min_media, 1)

    def test_minimax_reference_mode_uses_vendor_media_types(self):
        model = get_model("MiniMax/MiniMax-H3")
        capability = model.capability("r2v")

        self.assertIsNotNone(capability)
        self.assertEqual(
            [(item.kind, item.media_type, item.max_count) for item in capability.media_inputs],
            [
                ("image", "image_url", 9),
                ("video", "feature", 3),
                ("audio", "driving_audio", 3),
            ],
        )
        self.assertEqual(capability.min_media, 1)


class DashScopeMixedMediaPayloadTests(unittest.TestCase):
    def test_build_payload_preserves_each_provider_media_type(self):
        payload = build_payload(
            model="wan3.0-video-prime",
            prompt="让视频 1 的人物跟随音频 1 说话",
            resolution="1080P",
            duration=5,
            ratio="adaptive",
            media=[
                {"type": "reference_video", "url": "https://example.com/clip.mp4"},
                {"type": "reference_audio", "url": "https://example.com/voice.mp3"},
            ],
        )

        self.assertEqual(
            payload["input"]["media"],
            [
                {"type": "reference_video", "url": "https://example.com/clip.mp4"},
                {"type": "reference_audio", "url": "https://example.com/voice.mp3"},
            ],
        )


class HappyHorseProviderTests(unittest.TestCase):
    def test_compatible_mode_url_uses_the_same_host_for_async_video_tasks(self):
        self.assertEqual(
            _service_base_url(
                "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            "https://token-plan.cn-beijing.maas.aliyuncs.com",
        )

    def test_only_explicit_balance_errors_trigger_fallback(self):
        self.assertTrue(
            is_insufficient_balance({"code": "InsufficientBalance", "message": "余额不足"})
        )
        self.assertTrue(is_insufficient_balance({"message": "quota exhausted"}))
        self.assertFalse(is_insufficient_balance({"code": "InvalidApiKey"}))
        self.assertFalse(is_insufficient_balance({"code": "InvalidParameter"}))


if __name__ == "__main__":
    unittest.main()
