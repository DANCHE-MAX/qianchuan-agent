"""异常上报测试：任务崩了必须能在群里喊一声（这里降级到控制台验证格式）。"""
import os
import sys
import io
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
from agent import settings


def main():
    cfg = settings.load_config()

    # 把它替换成「没有 webhook 的配置」，这样上报会走控制台而不是真发群
    app._setup = lambda: (cfg, {}, None)

    with io.StringIO() as output, redirect_stdout(output):
        app._report_failure("daily", RuntimeError("模拟：CSV 文件不存在"))
        text = output.getvalue()
    assert "仅本地记录" in text
    assert "已上报" not in text

    print("OK test_failure: 异常上报链路可用（已降级为控制台）")


if __name__ == "__main__":
    main()
