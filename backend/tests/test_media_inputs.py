import unittest

from app.catalog import get_model
from app.dashscope import _chat_url, _service_base_url, build_payload, is_insufficient_balance


class ModelMediaCapabilityTests(unittest.TestCase):
    def test_only_wan_and_minimax_i2v_expose_end_frame(self):
        for model_id in ("wan3.0-video-prime", "MiniMax/MiniMax-H3"):
            model = get_model(model_id)
            kinds = [item.kind for item in model.capability("i2v").input_specs()]
            self.assertEqual(kinds, ["image", "end_frame"])

        model = get_model("happyhorse-1.1-i2v")
        self.assertEqual(
            [item.kind for item in model.capability("i2v").input_specs()],
            ["image"],
        )

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

    def test_i2v_payload_preserves_last_frame_provider_type(self):
        payload = build_payload(
            model="wan3.0-video-prime",
            prompt="从首帧过渡到尾帧",
            resolution="1080P",
            duration=5,
            media=[
                {"type": "first_frame", "url": "https://example.com/start.png"},
                {"type": "last_frame", "url": "https://example.com/end.png"},
            ],
        )
        self.assertEqual(
            payload["input"]["media"],
            [
                {"type": "first_frame", "url": "https://example.com/start.png"},
                {"type": "last_frame", "url": "https://example.com/end.png"},
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

    def test_chat_url_accepts_common_openai_compatible_base_urls(self):
        self.assertEqual(
            _chat_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/chat/completions",
        )
        self.assertEqual(
            _chat_url("https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(
            _chat_url("https://example.com/v1/chat/completions"),
            "https://example.com/v1/chat/completions",
        )


if __name__ == "__main__":
    unittest.main()
