"""
Chrome 启动器：以远程调试模式启动 Chrome，并等待 CDP 端口就绪。

核心原理：
  chrome --remote-debugging-port=9222 --user-data-dir=<profile>
  启动后，http://localhost:9222/json/version 会返回 WebSocket 端点，
  Playwright 通过 chromium.connect_over_cdp() 连接到同一个浏览器实例。
  这样用户手动操作的浏览器和 Playwright 读取 DOM 是同一个实例。
"""
import os
import subprocess
import time
import urllib.request

import config


def is_cdp_ready(port: int, timeout: float = 1.0) -> bool:
    """检查 CDP 端口是否就绪。"""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_chrome(
    chrome_path: str = config.CHROME_PATH,
    port: int = config.DEBUG_PORT,
    user_data_dir: str = config.USER_DATA_DIR,
    start_url: str = config.AMAP_URL,
) -> subprocess.Popen:
    """
    启动带远程调试端口的 Chrome，并加入反检测启动参数。

    反检测要点：
      - --disable-blink-features=AutomationControlled：隐藏 navigator.webdriver 标志
      - 不使用 --enable-automation（该标志会触发网站检测）
      - 添加真实 UA / 语言 / 插件环境参数
      - 禁用可能暴露自动化的实验特性

    返回 Popen 对象。调用方负责在退出时终止进程（或保留 Chrome 供用户继续使用）。
    """
    os.makedirs(user_data_dir, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # ===== 反检测核心参数 =====
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-infobars",
        # 模拟真实用户环境
        "--lang=zh-CN",
        "--disable-extensions-except=",  # 禁用扩展以免干扰（可按需放开）
        start_url,
    ]

    print(f"[Chrome] 正在启动，调试端口: {port}")
    print(f"[Chrome] 用户数据目录: {user_data_dir}")
    print("[Chrome] 已启用反检测启动参数")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待 CDP 就绪
    print("[Chrome] 等待 CDP 端口就绪...")
    for i in range(30):  # 最多等 30 秒
        if is_cdp_ready(port):
            print(f"[Chrome] CDP 就绪 ({i + 1}s)")
            return proc
        time.sleep(1)

    proc.terminate()
    raise RuntimeError("Chrome 启动超时，CDP 端口未就绪")


if __name__ == "__main__":
    p = launch_chrome()
    print("Chrome 已启动，按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.terminate()
        print("已退出")
