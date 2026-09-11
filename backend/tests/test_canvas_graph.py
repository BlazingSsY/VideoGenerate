import unittest

from app.canvas_graph import GraphValidationError, validate_graph


def prompt(node_id: str = "prompt-1") -> dict:
    return {
        "id": node_id,
        "type": "prompt",
        "position": {"x": 0, "y": 0},
        "data": {"text": "清晨的南方小镇"},
    }


def image(node_id: str = "image-1") -> dict:
    return {
        "id": node_id,
        "type": "image",
        "position": {"x": 0, "y": 160},
        "data": {"url": "/media/uploads/example.jpg", "name": "参考图"},
    }


def generate(
    node_id: str = "generate-1",
    model_id: str = "happyhorse-1.1-r2v",
) -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 400, "y": 0},
        "data": {
            "model": model_id,
            "capability": "r2v",
            "resolution": "1080P",
            "ratio": "16:9",
            "duration": 5,
            "watermark": True,
            "audio": None,
            "inlinePrompt": "",
        },
    }


def video(node_id: str = "video-1") -> dict:
    return {
        "id": node_id,
        "type": "video",
        "position": {"x": 0, "y": 320},
        "data": {"url": "/media/uploads/example.mp4", "name": "参考视频"},
    }


def audio(node_id: str = "audio-1") -> dict:
    return {
        "id": node_id,
        "type": "audio",
        "position": {"x": 0, "y": 480},
        "data": {"url": "/media/uploads/example.mp3", "name": "参考音频"},
    }


def output(node_id: str = "output-1") -> dict:
    return {
        "id": node_id,
        "type": "output",
        "position": {"x": 800, "y": 0},
        "data": {},
    }


class CanvasGraphValidationTests(unittest.TestCase):
    def test_accepts_typed_prompt_and_image_inputs(self):
        nodes = [prompt(), image(), generate()]
        edges = [
            {
                "id": "edge-text",
                "source": "prompt-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "prompt",
            },
            {
                "id": "edge-image",
                "source": "image-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "image_0",
            },
        ]

        validate_graph(nodes, edges)

    def test_accepts_video_audio_inputs_and_video_output(self):
        nodes = [
            prompt(),
            video(),
            audio(),
            generate(model_id="wan3.0-video-prime"),
            output(),
        ]
        edges = [
            {
                "id": "edge-text",
                "source": "prompt-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "prompt",
            },
            {
                "id": "edge-video",
                "source": "video-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "video_0",
            },
            {
                "id": "edge-audio",
                "source": "audio-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "audio_0",
            },
            {
                "id": "edge-output",
                "source": "generate-1",
                "source_handle": "out",
                "target": "output-1",
                "target_handle": "in",
            },
        ]

        validate_graph(nodes, edges)

    def test_rejects_a_mismatched_connection_type(self):
        nodes = [prompt(), generate()]
        edges = [
            {
                "id": "bad-edge",
                "source": "prompt-1",
                "source_handle": "out",
                "target": "generate-1",
                "target_handle": "image_0",
            }
        ]

        with self.assertRaisesRegex(GraphValidationError, "类型不匹配"):
            validate_graph(nodes, edges)


if __name__ == "__main__":
    unittest.main()
