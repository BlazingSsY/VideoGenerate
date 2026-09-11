"""独立的视频生成示例脚本。

正式 Web 应用请使用 ``backend/``；这个脚本只用于本地快速验证 DashScope。
"""

import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

API_BASE = "https://dashscope.aliyuncs.com/api/v1"
SUBMIT_URL = f"{API_BASE}/services/aigc/video-generation/video-synthesis"
OUTPUT_DIRECTORY = PROJECT_ROOT / "data" / "videos" / "legacy"
MAX_WAIT_SECONDS = 30 * 60


def api_error(action: str, data: dict) -> RuntimeError:
    output = data.get("output", {})
    code = data.get("code") or output.get("code") or "未知错误码"
    message = data.get("message") or output.get("message") or "接口未返回错误说明"
    request_id = data.get("request_id")
    detail = f"{action}：{code} - {message}"
    if request_id:
        detail += f"（request_id: {request_id}）"
    return RuntimeError(detail)


def read_api_response(action: str, response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError(f"{action}：接口返回了无法解析的数据。") from None
    if not response.is_success:
        raise api_error(action, data)
    return data


def main() -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY_WAN", "").strip()
    if not api_key:
        raise RuntimeError("请先设置环境变量 DASHSCOPE_API_KEY_WAN。")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    prompt = (
        "清晨的南方小镇老街，薄雾未散，写实电影风格，一家早餐铺亮着暖黄的灯，"
        "蒸笼冒着白气，市井烟火气。\n"
        "第1个镜头[0-3秒]：固定机位缓慢推近，老板掀开蒸笼，"
        "白色蒸汽扑面而来，包子在笼屉里冒着热气，晨光从街口洒进。\n"
        "第2个镜头[3-6秒]：特写，油条下锅在滚油中翻滚炸至金黄，"
        "滋滋作响；镜头切到豆浆倒入粗瓷碗，热气袅袅升起。\n"
        "第3个镜头[6-9秒]：中景，老顾客坐在矮凳上喝豆浆吃油条，"
        "老街坊路过互相打招呼，一只橘猫蹲在店铺门口舔爪子。\n"
        "第4个镜头[9-12秒]：晨光斜洒进巷子，老板用毛巾擦手，"
        "笑着望向街口，一辆老式自行车叮铃铃驶过，后座捆着青菜。\n"
        "第5个镜头[12-15秒]：镜头拉远升起，整条老街在晨光中慢慢醒来，"
        "炊烟与晨雾交织，画面温暖自然收尾。\n"
        "写实电影质感，暖色调，清晨柔光，市井烟火气，"
        "环境音为锅碗碰撞声、油锅滋滋声和街巷人声，治愈温暖不煽情。"
    )
    payload = {
        "model": "wan3.0-video-prime",
        "input": {"prompt": prompt},
        "parameters": {"resolution": "1080P", "ratio": "adaptive", "duration": 15},
    }

    with httpx.Client() as client:
        response = client.post(SUBMIT_URL, headers=headers, json=payload, timeout=60)
        submit_data = read_api_response("提交任务失败", response)
        task_id = submit_data.get("output", {}).get("task_id")
        if not task_id:
            raise api_error("提交任务失败", submit_data)
        print("任务已提交:", task_id)

        deadline = time.monotonic() + MAX_WAIT_SECONDS
        video_url = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("等待视频生成超时（30 分钟）。")
            response = client.get(
                f"{API_BASE}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=min(30, remaining),
            )
            task_data = read_api_response("查询任务失败", response)
            output = task_data.get("output", {})
            status = output.get("task_status")
            if not status:
                raise api_error("查询任务失败", task_data)
            print("状态:", status)
            if status == "SUCCEEDED":
                video_url = output.get("video_url")
                if not video_url:
                    raise RuntimeError("任务已成功，但接口没有返回视频地址。")
                break
            if status in {"FAILED", "CANCELED"}:
                raise api_error("视频生成失败", task_data)
            if status not in {"PENDING", "RUNNING"}:
                raise RuntimeError(f"接口返回了未知任务状态：{status}")
            time.sleep(min(20, max(0, deadline - time.monotonic())))

        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIRECTORY / f"output_{task_id}.mp4"
        partial_path = output_path.with_suffix(".mp4.part")
        try:
            timeout = httpx.Timeout(120, connect=10)
            with client.stream("GET", video_url, timeout=timeout) as download:
                download.raise_for_status()
                with partial_path.open("wb") as video_file:
                    for chunk in download.iter_bytes(chunk_size=1024 * 1024):
                        video_file.write(chunk)
            if partial_path.stat().st_size == 0:
                raise RuntimeError("视频下载失败：接口返回了空文件。")
            partial_path.replace(output_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise
        print("已保存到:", output_path)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as error:
        raise SystemExit(f"网络请求失败：{error}") from error
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
