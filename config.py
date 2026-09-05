"""
配置文件：Chrome 路径、调试端口、输出路径、DOM 选择器、正则表达式等。
"""
import os

# ============ Chrome 相关 ============
# macOS 默认 Chrome 路径
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 远程调试端口（CDP）
DEBUG_PORT = 9222

# Chrome 用户数据目录（持久化 Cookie，避免每次重新登录）
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(PROJECT_DIR, ".chrome-profile")

# 高德地图首页
AMAP_URL = "https://www.amap.com"

# ============ 代理配置（绕过 IP 风控）============
# 设置代理后，所有请求通过代理 IP 发出，可绕过高德的 IP 级限流。
# 留空字符串则不使用代理（直连）。
# 示例：
#   PROXY = "http://127.0.0.1:7890"          # 本地代理（如 Clash/V2Ray）
#   PROXY = "http://user:pass@ip:port"       # 带认证的代理
#   PROXY = "socks5://127.0.0.1:1080"         # SOCKS5 代理
PROXY = ""

# ============ 输出相关 ============
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
CSV_PATH = os.path.join(OUTPUT_DIR, "merchants.csv")
JSON_PATH = os.path.join(OUTPUT_DIR, "merchants.json")

# ============ 手机号正则 ============
# 匹配手机号：1开头，第二位3-9，共11位（必须完整11位，前后不能是数字）
PHONE_PATTERN = r"1[3-9]\d{9}"
# 匹配座机：必须带区号 0xx-xxxxxxx 或 0xxx-xxxxxxxx（可带分机）
# 注意：不匹配纯7-8位数字，避免误匹配页面上的其他数字（如天气、坐标等）
TEL_PATTERN = r"0\d{2,3}[-\s]?\d{7,8}(?:[-\s]转\d{1,5})?"

# 组合：优先手机号，其次座机（要求前后非数字边界）
PHONE_REGEX = rf"(?<!\d)({PHONE_PATTERN}|{TEL_PATTERN})(?!\d)"

# ============ 高德地图详情面板候选选择器（多策略，按优先级排序）============
# 高德详情面板 DOM 结构会随版本变化，这里提供多组候选，提取时依次尝试。
# 同时提供"基于文本"的兜底策略（见 amap_extractor.py），不依赖具体选择器。

# 详情面板容器候选（用于定位弹出的商户详情卡片）
DETAIL_PANEL_SELECTORS = [
    ".poi-detail",               # 常见详情面板
    ".detail-panel",             # 详情面板
    ".search-detail",            # 搜索详情
    ".amap-poi-detail",          # 高德 POI 详情
    ".poi-card",                 # 高德 POI 卡片
    ".poi-card-main",            # 高德卡片主体
    "[class*='detail'][class*='panel']",
    "[class*='poi-detail']",
    "[class*='PoiDetail']",
    "[class*='poi-card']",
    ".info-window",              # 信息窗口
    ".amap-info-window",
]

# 商户名称候选选择器（相对详情面板内）
NAME_SELECTORS = [
    ".poi-name",
    ".poi-card-name",            # 高德卡片名称
    ".detail-title",
    ".title",
    "h1",
    "h2",
    "[class*='name']",
    "[class*='title']",
    "[class*='Name']",
    "[class*='Title']",
]

# 电话号码候选选择器（相对详情面板内）
PHONE_SELECTORS = [
    ".poi-tel",
    ".poi-card-tel",             # 高德卡片电话
    ".tel",
    ".phone",
    "[class*='tel']",
    "[class*='phone']",
    "[class*='Tel']",
    "[class*='Phone']",
    "a[href^='tel:']",
]

# 轮询间隔（秒）：Python 端检查页面提取结果队列的频率
POLL_INTERVAL = 1.0

# ============ 风控检测与防误操作 ============
# 高德风控弹窗关键词（出现这些文案说明被限流）
RISK_KEYWORDS = [
    "哎呀",
    "有异常情况",
    "请稍后再试",
    "操作过于频繁",
    "访问过于频繁",
    "请完成验证",
    "安全验证",
    "滑动验证",
    "系统繁忙",
    "请求失败",
]

# 风控检测间隔（秒）：多久检查一次页面是否出现风控弹窗
RISK_CHECK_INTERVAL = 2.0

# 风控触发后自动等待恢复的时间（秒）
RISK_COOLDOWN_SECONDS = 90

# 风控后是否自动刷新页面（尝试解除风控）
RISK_AUTO_REFRESH = True

# 风控后是否尝试自动关闭弹窗
RISK_CLOSE_POPUP = True

# 采集速度提醒：每分钟点击超过该次数时提醒用户放慢速度
SPEED_WARN_PER_MINUTE = 15

# 每次成功采集后建议的最小间隔提示（秒）
MIN_CLICK_INTERVAL_HINT = 5.0

# ============ 人类行为模拟与批次控制 ============
# 是否启用人类行为模拟（采集后随机滚动、鼠标移动等）
HUMAN_BEHAVIOR_ENABLED = True

# 每采集多少条后强制暂停（模拟人类休息，避免连续操作触发风控）
BATCH_SIZE = 15

# 批次暂停时间（秒）
BATCH_PAUSE_SECONDS = 30
