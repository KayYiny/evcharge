# -*- coding: utf-8 -*-
"""命令行测试车牌识别（不启动图形界面）。

用法::

    python scripts/test_plate.py 车牌照片.jpg

会读取本机设置里的 AI 配置（URL / API Key / 视觉模型），
把图片送去识别并打印结果。用来在不打开界面的情况下确认配置是否可用。

如果还没写进设置，也可以临时用环境变量覆盖::

    OPENAI_API_KEY=你的Key python scripts/test_plate.py 车牌照片.jpg
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evcharge.infrastructure.plate_recognition import (  # noqa: E402
    PlateRecognitionError,
    recognize_plate,
)
from evcharge.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/test_plate.py <图片路径>")
        return 2

    image = Path(sys.argv[1])
    settings = load_settings()

    # 允许用环境变量临时覆盖，方便不修改设置就能试 Key
    env_key = os.getenv("OPENAI_API_KEY")
    if env_key:
        settings.api_key = env_key

    print(f"接口：{settings.chat_url or '（未配置）'}")
    print(f"视觉模型：{settings.effective_vision_model or '（未配置）'}")
    print(f"图片：{image}")
    print(f"API Key：{'已配置' if settings.api_key.strip() else '未配置'}")
    print("-" * 48)

    try:
        plate, raw = recognize_plate(image, settings)
    except PlateRecognitionError as exc:
        print(f"❌ 识别失败：{exc}")
        return 1
    print(f"✅ 识别结果：{plate}")
    print(f"（原始文本：{raw}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
