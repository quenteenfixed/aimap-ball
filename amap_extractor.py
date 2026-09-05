"""
高德地图商户信息提取器。

核心机制：
  1. Python 端通过 Playwright 将一段 JS 注入到高德页面。
  2. 该 JS 使用 MutationObserver 监听 DOM 变化，当商户详情卡片出现时，
     自动提取商户名和电话号码，存入 window.__merchantQueue 队列。
  3. Python 端轮询该队列，将新数据去重后保存到本地 CSV / JSON。

提取采用"多策略 + 正则兜底"，不依赖单一选择器，以应对高德 DOM 结构变化。
"""
import json
import os
import time
from typing import List, Dict, Optional

import config

# ============================================================
# 注入到页面的 JavaScript：MutationObserver + 多策略提取
# ============================================================
INJECT_JS = r"""
(function () {
  if (window.__merchantObserverInstalled) return;
  window.__merchantObserverInstalled = true;

  // 数据队列（Python 端轮询读取）
  window.__merchantQueue = window.__merchantQueue || [];
  // 已提取集合（去重）
  window.__seenMerchants = window.__seenMerchants || new Set();

  // 手机号 / 座机正则（座机必须带区号，避免误匹配天气/坐标等数字）
  var PHONE_RE = /1[3-9]\d{9}/;
  var TEL_RE = /0\d{2,3}[-\s]?\d{7,8}(?:[-\s]转\d{1,5})?/;
  var COMBINED_RE = /(?<!\d)(1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}(?:[-\s]转\d{1,5})?)(?!\d)/;

  // 详情面板候选选择器
  var PANEL_SELECTORS = [
    '.poi-detail', '.detail-panel', '.search-detail',
    '.amap-poi-detail', '.poi-card', '.poi-card-main',
    "[class*='detail'][class*='panel']",
    "[class*='poi-detail']", "[class*='PoiDetail']",
    "[class*='poi-card']",
    '.info-window', '.amap-info-window'
  ];
  // 名称候选选择器（相对面板内）
  var NAME_SELECTORS = [
    '.poi-name', '.poi-card-name', '.detail-title', '.title', 'h1', 'h2',
    "[class*='name']", "[class*='title']",
    "[class*='Name']", "[class*='Title']"
  ];
  // 电话候选选择器（相对面板内）
  var PHONE_SELECTORS = [
    '.poi-tel', '.poi-card-tel', '.tel', '.phone',
    "[class*='tel']", "[class*='phone']",
    "[class*='Tel']", "[class*='Phone']", "a[href^='tel:']"
  ];

  function visible(el) {
    if (!el) return false;
    var style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
  }

  function cleanText(s) {
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  // 从元素文本中提取第一个手机号或座机（带数字边界，避免截断匹配）
  function extractPhoneFromText(text) {
    if (!text) return '';
    var m = text.match(COMBINED_RE);
    if (m) return m[1] || m[0];
    return '';
  }

  // 尝试在面板内用候选选择器找名称
  function findNameInPanel(panel) {
    for (var i = 0; i < NAME_SELECTORS.length; i++) {
      var el = panel.querySelector(NAME_SELECTORS[i]);
      if (el && visible(el)) {
        var t = cleanText(el.textContent);
        if (t && t.length <= 50) return t;
      }
    }
    // 兜底：取面板内第一个可见的粗体/标题文本
    var strongs = panel.querySelectorAll('b, strong, .title, [class*="title"]');
    for (var j = 0; j < strongs.length; j++) {
      var ts = cleanText(strongs[j].textContent);
      if (ts && ts.length <= 50 && ts.length > 1) return ts;
    }
    return '';
  }

  // 尝试在面板内用候选选择器或正则找电话
  function findPhoneInPanel(panel) {
    // 优先 tel: 链接
    var telLinks = panel.querySelectorAll("a[href^='tel:']");
    for (var i = 0; i < telLinks.length; i++) {
      var href = telLinks[i].getAttribute('href') || '';
      var num = href.replace(/^tel:/, '').replace(/[^\d]/g, '');
      if (num.length >= 7) return num;
    }
    // 候选选择器
    for (var j = 0; j < PHONE_SELECTORS.length; j++) {
      var el = panel.querySelector(PHONE_SELECTORS[j]);
      if (el) {
        var p = extractPhoneFromText(el.textContent);
        if (p) return p;
      }
    }
    // 正则扫描面板全部文本
    return extractPhoneFromText(panel.textContent);
  }

  // 策略：定位详情面板 → 提取名称+电话
  function tryExtractFromPanel() {
    for (var i = 0; i < PANEL_SELECTORS.length; i++) {
      var panels = document.querySelectorAll(PANEL_SELECTORS[i]);
      for (var j = 0; j < panels.length; j++) {
        var panel = panels[j];
        if (!visible(panel)) continue;
        var name = findNameInPanel(panel);
        var phone = findPhoneInPanel(panel);
        if (name && phone) {
          return { name: name, phone: phone, source: 'panel' };
        }
      }
    }
    return null;
  }

  // 兜底策略：扫描整个可见 DOM，找到电话后向父级回溯找名称
  function tryExtractByTextScan() {
    // 收集所有包含电话的可见文本节点的父元素
    var walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: function (node) {
          if (!node.nodeValue || !COMBINED_RE.test(node.nodeValue)) {
            return NodeFilter.FILTER_REJECT;
          }
          // 重置 lastIndex（test 会改变带 g 标志的正则状态，但这里没 g，保险起见）
          COMBINED_RE.lastIndex = 0;
          var p = node.parentElement;
          if (!p || !visible(p)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      }
    );

    var results = [];
    var node;
    while ((node = walker.nextNode())) {
      var phone = extractPhoneFromText(node.nodeValue);
      if (!phone) continue;
      // 向父级找名称：最多回溯 8 层
      var parent = node.parentElement;
      var name = '';
      for (var depth = 0; depth < 8 && parent; depth++) {
        // 在该父元素内找标题/名称
        var nameEl = parent.querySelector(NAME_SELECTORS.join(','));
        if (nameEl && visible(nameEl)) {
          var t = cleanText(nameEl.textContent);
          if (t && t.length <= 50 && t !== phone) {
            name = t;
            break;
          }
        }
        parent = parent.parentElement;
      }
      if (name) {
        results.push({ name: name, phone: phone, source: 'text-scan' });
      }
    }
    return results.length ? results[0] : null;
  }

  function tryExtract() {
    var r = tryExtractFromPanel();
    if (r) return r;
    return tryExtractByTextScan();
  }

  // 去重并入队
  function pushResult(r) {
    if (!r || !r.name || !r.phone) return;
    var key = r.name + '||' + r.phone;
    if (window.__seenMerchants.has(key)) return;
    window.__seenMerchants.add(key);
    window.__merchantQueue.push({
      name: r.name,
      phone: r.phone,
      source: r.source,
      timestamp: new Date().toISOString()
    });
    console.log('[采集器] 已采集:', r.name, r.phone, '(' + r.source + ')');
  }

  // 节流：DOM 变化频繁，延迟 400ms 后再扫描
  var timer = null;
  function scheduleExtract() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      var r = tryExtract();
      if (r) pushResult(r);
    }, 400);
  }

  // 监听 DOM 变化
  var observer = new MutationObserver(function (mutations) {
    scheduleExtract();
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true
  });

  // 立即尝试一次（页面可能已有详情卡片）
  scheduleExtract();

  console.log('[采集器] MutationObserver 已安装，开始监听商户详情卡片...');
})();
"""


# ============================================================
# Python 端：安装观察者、轮询、保存
# ============================================================

def install_observer(page) -> None:
    """将提取 JS 注入到页面，安装 MutationObserver。"""
    page.evaluate(INJECT_JS)


def check_risk(page) -> Optional[str]:
    """
    检测页面是否出现高德风控弹窗。

    返回匹配到的风控关键词，未检测到返回 None。
    """
    try:
        keywords_js = json.dumps(config.RISK_KEYWORDS, ensure_ascii=False)
        result = page.evaluate(
            f"""
            (keywords) => {{
                // 检查页面可见文本中是否包含风控关键词
                const bodyText = (document.body.innerText || '').slice(0, 3000);
                for (const kw of keywords) {{
                    if (bodyText.includes(kw)) {{
                        return kw;
                    }}
                }}
                // 也检查弹窗/遮罩层的文本
                const masks = document.querySelectorAll('.mask, [class*="mask"], [class*="dialog"], [class*="modal"], [role="dialog"]');
                for (const m of masks) {{
                    const t = (m.textContent || '');
                    for (const kw of keywords) {{
                        if (t.includes(kw)) return kw;
                    }}
                }}
                return null;
            }}
            """,
            config.RISK_KEYWORDS,
        )
        return result
    except Exception:
        return None


def refresh_page(page) -> None:
    """刷新页面（用于风控后尝试恢复）。"""
    try:
        page.reload(wait_until="domcontentloaded", timeout=15000)
        print("[风控] 页面已刷新")
    except Exception as e:
        print(f"[风控] 刷新页面失败: {e}")


def close_risk_popup(page) -> bool:
    """
    尝试自动关闭风控弹窗。返回是否成功关闭。
    高德风控弹窗通常有遮罩层和"知道了"/"确定"按钮。
    """
    try:
        result = page.evaluate(
            """
            () => {
                // 查找弹窗的关闭按钮
                const closeSelectors = [
                    '.close-btn', '.close', '.btn-close', '[class*="close"]',
                    '.amap-close', '.dialog-close', '.modal-close',
                    'button:has-text("知道了")', 'button:has-text("确定")',
                    'button:has-text("我知道了")', 'button:has-text("关闭")',
                ];
                for (const sel of closeSelectors) {
                    try {
                        const btn = document.querySelector(sel);
                        if (btn && btn.offsetParent !== null) {
                            btn.click();
                            return sel;
                        }
                    } catch (e) {}
                }
                // 兜底：点击遮罩层外部
                const masks = document.querySelectorAll('.mask, [class*="mask"], [class*="overlay"]');
                for (const m of masks) {
                    if (m.offsetParent !== null) {
                        // 点击遮罩层外围
                        const rect = m.getBoundingClientRect();
                        const evt = new MouseEvent('click', {
                            clientX: rect.left + 5,
                            clientY: rect.top + 5,
                            bubbles: true
                        });
                        m.dispatchEvent(evt);
                        return 'mask-click';
                    }
                }
                return null;
            }
            """
        )
        if result:
            print(f"[风控] 已尝试关闭弹窗: {result}")
            return True
        return False
    except Exception as e:
        print(f"[风控] 关闭弹窗异常: {e}")
        return False


def simulate_human_behavior(page) -> None:
    """
    模拟人类行为：随机滚动页面、移动鼠标，让操作节奏不那么机械。
    在每次成功采集后调用，降低被行为风控检测的概率。
    """
    if not config.HUMAN_BEHAVIOR_ENABLED:
        return
    import random
    try:
        # 随机滚动（小幅度，模拟查看详情时的自然滚动）
        scroll_y = random.randint(-100, 100)
        page.evaluate(f"window.scrollBy(0, {scroll_y})")
        # 随机移动鼠标到页面某个位置
        x = random.randint(100, 800)
        y = random.randint(100, 600)
        page.mouse.move(x, y)
        # 极短随机停顿
        time.sleep(random.uniform(0.1, 0.4))
    except Exception:
        pass  # 人类行为模拟失败不影响采集


def fetch_queue(page) -> List[Dict]:
    """读取并清空页面上的采集队列。"""
    result = page.evaluate(
        """
        () => {
            const q = window.__merchantQueue || [];
            window.__merchantQueue = [];
            return q;
        }
        """
    )
    return result or []


def load_existing() -> List[Dict]:
    """加载已有数据（用于去重）。"""
    if os.path.exists(config.JSON_PATH):
        try:
            with open(config.JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_records(records: List[Dict]) -> None:
    """保存记录到 CSV 和 JSON。"""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # 保存 JSON
    with open(config.JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # 保存 CSV
    import csv

    with open(config.CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "phone", "source", "timestamp"])
        writer.writeheader()
        for r in records:
            writer.writerow({
                "name": r.get("name", ""),
                "phone": r.get("phone", ""),
                "source": r.get("source", ""),
                "timestamp": r.get("timestamp", ""),
            })


def run_collector(page, context=None, stop_event=None) -> int:
    """
    持续轮询页面采集队列，保存新数据。

    新增能力：
      - 风控弹窗检测：检测到"哎呀，有异常情况"等文案时自动暂停、冷却、刷新页面
      - 采集速度提醒：每分钟点击过快时提示用户放慢节奏
      - 上下文自动恢复：页面导航导致执行上下文销毁时自动重新注入观察者

    返回采集到的总记录数（本次运行新增）。
    """
    import time as _time
    import collections

    existing = load_existing()
    existing_keys = {(r["name"], r["phone"]) for r in existing}
    all_records = list(existing)
    new_count = 0

    # 速度统计：记录每次采集的时间戳
    click_timestamps = collections.deque()
    # 风控检测计数器（避免每次轮询都检测，降低开销）
    risk_check_counter = 0
    risk_check_every = max(1, int(config.RISK_CHECK_INTERVAL / config.POLL_INTERVAL))

    print(f"[采集器] 已有 {len(existing)} 条记录，开始监听新数据...")
    print(f"[采集器] 输出目录: {config.OUTPUT_DIR}")
    print("[采集器] 提示：在高德地图中搜索并点击商户卡片，详情弹出后将自动采集。")
    print(f"[采集器] 风控保护：检测异常→关弹窗→冷却{config.RISK_COOLDOWN_SECONDS}s+抖动→刷新页面")
    print(f"[采集器] 速度提醒：每分钟超过 {config.SPEED_WARN_PER_MINUTE} 条会提示放慢节奏")
    print(f"[采集器] 批次控制：每采集 {config.BATCH_SIZE} 条暂停 {config.BATCH_PAUSE_SECONDS}s（模拟人类休息）")
    print(f"[采集器] 人类行为模拟：{'已启用' if config.HUMAN_BEHAVIOR_ENABLED else '已关闭'}")
    print("[采集器] 按 Ctrl+C 停止采集。\n")

    def get_amap_page():
        """从上下文中重新获取高德页面。"""
        if context is None:
            return page
        for p in context.pages:
            try:
                if "amap.com" in p.url:
                    return p
            except Exception:
                continue
        return context.pages[0] if context.pages else page

    def handle_risk(kw: str):
        """处理风控：关闭弹窗 → 冷却（带随机抖动）→ 刷新页面 → 重新注入。"""
        nonlocal page
        import random
        print(f"\n{'!' * 50}")
        print(f"[风控] 检测到限流关键词: \"{kw}\"")
        # 1. 尝试关闭弹窗
        if config.RISK_CLOSE_POPUP:
            close_risk_popup(page)
            _time.sleep(1)
        # 2. 冷却等待（加随机抖动，避免固定间隔被识别）
        cooldown = config.RISK_COOLDOWN_SECONDS + random.randint(0, 30)
        print(f"[风控] 系统将暂停 {cooldown} 秒（含随机抖动）后自动恢复...")
        print(f"{'!' * 50}\n")
        _time.sleep(cooldown)
        # 3. 刷新页面尝试解除风控
        if config.RISK_AUTO_REFRESH:
            refresh_page(page)
            _time.sleep(3)
            # 刷新后重新注入观察者
            try:
                install_observer(page)
                print("[风控] 已重新注入观察者，继续采集。")
            except Exception as e:
                print(f"[风控] 重新注入失败: {e}")

    def check_speed():
        """检查采集速度，过快时提醒。"""
        now = _time.time()
        # 清理超过 60 秒的时间戳
        while click_timestamps and now - click_timestamps[0] > 60:
            click_timestamps.popleft()
        if len(click_timestamps) > config.SPEED_WARN_PER_MINUTE:
            print(f"\n⚠️  [速度提醒] 最近 1 分钟已采集 {len(click_timestamps)} 条，"
                  f"建议放慢节奏（每 {config.MIN_CLICK_INTERVAL_HINT}s 点击一个），避免触发风控。\n")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break

            # 风控检测（按间隔执行）
            risk_check_counter += 1
            if risk_check_counter >= risk_check_every:
                risk_check_counter = 0
                kw = check_risk(page)
                if kw:
                    handle_risk(kw)
                    continue

            try:
                queue = fetch_queue(page)
            except Exception as e:
                err_msg = str(e)
                if "context was destroyed" in err_msg or "Execution context" in err_msg:
                    # 页面导航导致上下文销毁，重新获取页面并注入观察者
                    print("[采集器] 页面上下文已销毁，正在恢复...")
                    page = get_amap_page()
                    try:
                        install_observer(page)
                        print("[采集器] 已重新注入观察者，继续监听。")
                    except Exception as e2:
                        print(f"[采集器] 重新注入失败: {e2}")
                else:
                    print(f"[采集器] 读取队列失败: {e}")
                time.sleep(config.POLL_INTERVAL)
                continue

            for item in queue:
                key = (item["name"], item["phone"])
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                all_records.append(item)
                new_count += 1
                click_timestamps.append(_time.time())
                print(f"  ✓ {item['name']}  |  {item['phone']}  ({item.get('source', '')})")

                # 每采集一条后模拟人类行为（随机滚动+鼠标移动）
                simulate_human_behavior(page)

                # 批次强制暂停：每 BATCH_SIZE 条暂停一段时间，模拟人类休息
                if new_count > 0 and new_count % config.BATCH_SIZE == 0:
                    import random
                    pause = config.BATCH_PAUSE_SECONDS + random.randint(0, 15)
                    print(f"\n😴 [批次休息] 已采集 {new_count} 条，暂停 {pause} 秒（模拟人类休息）...\n")
                    _time.sleep(pause)

            if queue:
                save_records(all_records)
                check_speed()

            time.sleep(config.POLL_INTERVAL)
    except KeyboardInterrupt:
        pass

    save_records(all_records)
    print(f"\n[采集器] 本次新增 {new_count} 条，累计 {len(all_records)} 条。")
    print(f"[采集器] 数据已保存到:")
    print(f"  - {config.CSV_PATH}")
    print(f"  - {config.JSON_PATH}")
    return new_count
