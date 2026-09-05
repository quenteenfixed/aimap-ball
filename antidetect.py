"""
反检测模块：在页面加载前注入 JavaScript，隐藏浏览器自动化指纹。

参考 browser-use / puppeteer-extra-plugin-stealth 的核心策略，
覆盖以下常见检测点：
  1. navigator.webdriver → undefined
  2. navigator.plugins / mimeTypes → 真实插件列表
  3. window.chrome → 真实 Chrome 对象
  4. permissions API → 正常返回
  5. navigator.languages → 中文环境
  6. WebGL 厂商/渲染器 → 常见显卡值
  7. toString 原生函数检测 → 还原为 native code
"""

# 反检测 init script（在页面任何脚本执行前注入）
ANTIDETECT_JS = r"""
(() => {
  if (window.__antidetectInjected) return;
  window.__antidetectInjected = true;

  // ---------- 1. 隐藏 navigator.webdriver ----------
  // 先尝试删除，再用 defineProperty 兜底为 undefined
  try {
    delete navigator.webdriver;
  } catch (e) {}
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true
  });

  // ---------- 2. 还原原生函数 toString（防止检测被 hook） ----------
  const originalToString = Function.prototype.toString;
  Function.prototype.toString = new Proxy(originalToString, {
    apply(target, thisArg, args) {
      const result = Reflect.apply(target, thisArg, args);
      // 对被 hook 的函数返回 native code 字符串
      if (typeof thisArg === 'function') {
        const src = String(thisArg);
        if (src.includes('[native code]') || src === 'function () { [native code] }') {
          return 'function () { [native code] }';
        }
      }
      return result;
    }
  });

  // ---------- 3. 修复 window.chrome 对象（普通网页有 chrome 对象但无 runtime） ----------
  if (!window.chrome) {
    window.chrome = {};
  }
  // 补充真实 Chrome 特有的函数（部分反爬会检测这些是否存在）
  if (!window.chrome.csi) {
    window.chrome.csi = function () {
      return {
        startE: Date.now() - Math.floor(Math.random() * 10000),
        onloadT: Date.now() - Math.floor(Math.random() * 5000),
        pageT: Math.floor(Math.random() * 1000),
        tran: 15
      };
    };
  }
  if (!window.chrome.loadTimes) {
    window.chrome.loadTimes = function () {
      const now = Date.now() / 1000;
      return {
        commitLoadTime: now - 2,
        connectionInfo: "h2",
        finishDocumentLoadTime: now - 1,
        finishLoadTime: now,
        firstPaintAfterLoadTime: now - 0.5,
        firstPaintTime: now - 1.5,
        navigationType: "Other",
        npnNegotiatedProtocol: "h2",
        requestTime: now - 3,
        startLoadTime: now - 2.5,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true
      };
    };
  }
  if (!window.chrome.app) {
    window.chrome.app = {
      isInstalled: false,
      getDetails: function () { return null; },
      getIsInstalled: function () { return false; },
    };
  }
  // 普通网页不应有 chrome.runtime（只有扩展页面才有），不添加
  try { Object.defineProperty(window, 'chrome', { value: window.chrome, configurable: true, writable: true }); } catch (e) {}

  // ---------- 4. 伪造 navigator.plugins 和 mimeTypes ----------
  if (!navigator.plugins || navigator.plugins.length === 0) {
    const pluginData = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    const plugins = [];
    for (const p of pluginData) {
      const plugin = document.createElement('embed');
      Object.defineProperty(plugin, 'name', { value: p.name, configurable: true });
      Object.defineProperty(plugin, 'filename', { value: p.filename, configurable: true });
      Object.defineProperty(plugin, 'description', { value: p.description, configurable: true });
      plugins.push(plugin);
    }
    Object.defineProperty(navigator, 'plugins', {
      get: () => plugins,
      configurable: true
    });
  }

  // ---------- 5. navigator.languages ----------
  Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en'],
    configurable: true
  });

  // ---------- 6. permissions API 正常返回 ----------
  const originalQuery = window.navigator.permissions ? window.navigator.permissions.query.bind(window.navigator.permissions) : null;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) => {
      if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery(parameters);
    };
  }

  // ---------- 7. WebGL 指纹（让真实 GPU 显示，不伪造） ----------
  // 注意：移除了 --enable-unsafe-swiftshader 后，Chrome 使用真实 GPU，
  // 不再伪造 WebGL 厂商/渲染器，保持真实性。

  // ---------- 8. 覆盖 Notification.permission 默认值 ----------
  try {
    if (!('permission' in Notification)) {
      Object.defineProperty(Notification, 'permission', {
        get: () => 'default',
        configurable: true
      });
    }
  } catch (e) {}

  // ---------- 9. 修复 navigator.platform ----------
  Object.defineProperty(navigator, 'platform', {
    get: () => 'MacIntel',
    configurable: true
  });

  console.log('[反检测] 自动化指纹已隐藏');
})();
"""


def inject_antidetect(context) -> None:
    """
    将反检测脚本注入到浏览器上下文的每个新页面。
    使用 add_init_script 确保在页面任何脚本之前执行。
    """
    context.add_init_script(ANTIDETECT_JS)
    print("[反检测] init script 已注入（将在每个新页面加载前执行）")


def verify_antidetect(page) -> dict:
    """
    验证反检测脚本是否生效，返回各项检测结果。
    """
    result = page.evaluate(
        """
        () => {
            return {
                webdriver: navigator.webdriver,
                webdriverType: typeof navigator.webdriver,
                hasChrome: !!window.chrome,
                pluginCount: navigator.plugins ? navigator.plugins.length : 0,
                languages: navigator.languages,
                platform: navigator.platform,
            };
        }
        """
    )
    return result
