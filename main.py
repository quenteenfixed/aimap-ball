"""
高德地图商户信息自动采集系统 — 主入口。

工作流程：
  1. Playwright 直接启动 Chrome（持久化用户数据，headless=False，用户可手动操作）
  2. 在 context 上注入反检测 init script（隐藏自动化指纹）
  3. 打开高德地图，注入 MutationObserver 监听商户详情卡片
  4. 用户手动搜索"中国体育彩票"并点击商户卡片
  5. 详情卡片弹出时自动采集商户名+电话，保存到本地 CSV/JSON

使用方法：
  python main.py
  # 然后在打开的 Chrome 中手动操作：搜索 → 点击商户卡片
  # 采集到的数据会实时打印并保存到 output/ 目录
  # 按 Ctrl+C 停止
"""
import sys
import time

from playwright.sync_api import sync_playwright

import config
import amap_extractor
import antidetect


def main():
    print("=" * 60)
    print("  高德地图商户信息自动采集系统（反检测增强版）")
    print("=" * 60)
    print()

    try:
        with sync_playwright() as p:
            # 1. Playwright 直接启动 Chrome（不通过 CDP，减少暴露）
            print("[Playwright] 正在启动 Chrome（用户可手动操作）...")
            # 移除 Playwright 默认添加的自动化特征参数（这些参数会被网站检测）
            suspicious_args = [
                "--use-mock-keychain",           # 模拟钥匙串
                "--no-sandbox",                   # 常见于自动化
                "--metrics-recording-only",       # 禁止发送指标
                "--enable-unsafe-swiftshader",    # 软件渲染
                "--disable-extensions",           # 真实用户有扩展
                "--disable-popup-blocking",       # 真实 Chrome 拦截弹窗
                "--disable-breakpad",             # 禁用崩溃报告
                "--password-store=basic",         # 密码存储异常
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-hang-monitor",
                "--force-color-profile=srgb",
                "--export-tagged-pdf",
                "--allow-pre-commit-input",
                "--disable-ipc-flooding-protection",
                "--disable-prompt-on-repost",
                "--no-service-autorun",
                "--disable-search-engine-choice-screen",
                "--disable-sync",                   # 真实用户可能开启同步
                "--disable-field-trial-config",     # Playwright 特有
                "--unsafely-disable-devtools-self-xss-warnings",
                "--edge-skip-compat-layer-relaunch",
                "--disable-component-extensions-with-background-pages",
                "--disable-back-forward-cache",
                "--disable-edgeupdater",
            ]
            launch_kwargs = dict(
                user_data_dir=config.USER_DATA_DIR,
                channel="chrome",
                headless=False,
                ignore_default_args=suspicious_args,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--lang=zh-CN",
                ],
                viewport={"width": 1280, "height": 800},
            )
            # 代理配置（绕过 IP 风控）
            if config.PROXY:
                launch_kwargs["proxy"] = {"server": config.PROXY}
                print(f"[Playwright] 已启用代理: {config.PROXY}")
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            print(f"[Playwright] Chrome 已启动，用户数据目录: {config.USER_DATA_DIR}")
            print("[Playwright] 已移除自动化特征参数")

            # 2. 注入反检测 init script（在每个新页面加载前执行）
            antidetect.inject_antidetect(context)

            # 3. 打开高德地图
            page = context.new_page()
            page.goto(config.AMAP_URL, wait_until="domcontentloaded")
            print(f"[Playwright] 已打开高德地图: {page.url}")

            # 等待页面加载（高德是单页应用，给一些时间）
            time.sleep(3)

            # 4. 验证反检测是否生效
            print("\n[反检测] 验证自动化指纹隐藏情况：")
            verify = antidetect.verify_antidetect(page)
            print(f"  navigator.webdriver = {verify['webdriver']}  (type: {verify['webdriverType']})")
            print(f"  window.chrome 存在 = {verify['hasChrome']}  (应为 True)")
            print(f"  navigator.plugins 数量 = {verify['pluginCount']}  (应 > 0)")
            print(f"  navigator.languages = {verify['languages']}")
            if verify['webdriverType'] == 'undefined':
                print("  ✓ navigator.webdriver 已隐藏为 undefined")
            elif verify['webdriver'] is not True:
                print("  ✓ navigator.webdriver 非 true，可绕过 === true 检测")

            # 5. 注入 MutationObserver 提取器
            print("\n[采集器] 正在注入 DOM 监听脚本...")
            amap_extractor.install_observer(page)
            print("[采集器] 注入完成。")

            # 6. 运行采集循环（含风控检测与速度提醒）
            print()
            print("-" * 60)
            print("  现在请在 Chrome 中手动操作：")
            print("    1. 在搜索框输入：中国体育彩票")
            print("    2. 点击搜索结果中的商户卡片")
            print("    3. 商户详情弹出后，系统将自动采集名称和电话")
            print("  注意：建议每个商户间隔 5 秒以上，每 15 条系统会自动暂停休息。")
            print("-" * 60)
            print()

            amap_extractor.run_collector(page, context=context)

            # 采集结束
            print("[Playwright] 正在关闭 Chrome...")
            context.close()

    except KeyboardInterrupt:
        print("\n[系统] 收到中断信号，正在退出...")
    except Exception as e:
        print(f"[系统] 发生错误: {e}")
        import traceback
        traceback.print_exc()

    print("[系统] 程序结束。")


if __name__ == "__main__":
    main()
