/**
 * 益食安康 — 适老化偏好（R1，2026-10-16）
 *
 * 现在管两件事，都存 localStorage、都在 <head> 里**同步**应用：
 *
 *   1) 阅读字号
 *      用户在「设置」页拖滑块 → 写 CSS 变量 --reading-size / --reading-scale
 *      → 全站字号同比缩放（6 档 token 乘倍率；页面里写死的 rem 字号靠 html
 *        的 font-size 一起乘倍率）。
 *      拖动中（input）只改「效果预览」那一段；松手（change）才全站生效。
 *
 *   2) 高对比度
 *      开关打开 → 给 <html> 加 data-contrast="high"，样式表里那一块负责
 *      加深文字灰阶、加深主色按钮、加粗描边。
 *
 *   3) 导航栏快捷字号按钮（A− / A+，2026-11-01 加）
 *      不用登录、不用进「设置」页，任何页面顶部都能直接调。
 *      70 岁老人不会去"设置里找滑块"，入口必须第一屏可见。
 *      点一下 ±2px，到 16 / 40 两端自动禁用对应按钮。
 *
 *   4) 跟随系统设置（第 5 步，2026-11-01）
 *      系统里开了「增强对比度」→ 默认就当高对比开着（除非用户在本站明确关过）。
 *      「减弱动态效果」不需要 JS，纯 CSS 媒体查询就够了（style.css 末尾）。
 *      为什么高对比要 JS 参与：纯 CSS 的 @media 没法被用户"关掉" ——
 *      用户在设置页把开关拨回去、系统又开着增强对比，页面会纹丝不动，
 *      看起来就是开关坏了。所以"手动选择"必须能手 win 系统默认值。
 *
 * 为什么放在 <head> 且同步执行：正文渲染前就把属性 / 变量写好，
 * 否则打开页面会先小后大、先浅后深地闪一下。
 *
 * 依赖：无（不引任何第三方库）。localStorage 不可用（隐私模式）时
 * 自动回落到默认值，不报错。
 */
(function () {
    'use strict';

    // ---- 1) 阅读字号 ----------------------------------------------------
    // SCALE_BASE_PX 是「倍率 = 1」的锚点，只影响 CSS 换算，**不是**默认值。
    // 它必须保持 18：style.css 里 6 档字号 token 全是「14px × 倍率」这样写的，
    // 动这个数等于全站重新排版一遍。
    var SCALE_BASE_PX = 18;
    // DEFAULT_PX 是用户没调过时实际用的值（2026-11-01：18 → 20）。
    // 20÷18 ≈ 1.11，正文从 14px 变成约 15.6px —— 接近「正文不小于 16px」这条
    // 老年阅读的通用基线。真要再大，让用户按 A+，不要一次把默认值抬到 22+，
    // 否则所有卡片/表格在两栏布局下都要重新验一遍。
    var DEFAULT_PX = 20;
    var MIN_PX = 16;
    // 上限 32 → 40（2026-11-01）：40px 时正文约 31px，一屏大约放得下 30~40 个字，
    // 是「70 岁老人不戴镜子也能读」的量级。iOS「更大字体」的无障碍档位也是这个思路：
    // 常规档到头后，另给一段更激进的范围。
    var MAX_PX = 40;
    var SIZE_KEY = 'ysak.readingSize';

    // ---- 2) 高对比度 ----------------------------------------------------
    var CONTRAST_KEY = 'ysak.contrast';
    var CONTRAST_ON = 'high';
    var CONTRAST_OFF = 'normal';        // 显式关闭：用来盖住系统「增强对比度」
    // 系统偏好查询串（第 5 步）。样式表里的同名媒体查询必须一致。
    var SYS_CONTRAST_QUERY = '(prefers-contrast: more)';

    function clamp(px) {
        px = parseInt(px, 10);
        if (isNaN(px)) return DEFAULT_PX;
        return Math.min(MAX_PX, Math.max(MIN_PX, px));
    }

    function readKey(key, fallback) {
        try {
            var raw = window.localStorage.getItem(key);
            if (raw === null || raw === '') return fallback;
            return raw;
        } catch (e) {
            return fallback;   // 隐私模式 / 存储被禁用
        }
    }

    function writeKey(key, value) {
        try {
            window.localStorage.setItem(key, String(value));
        } catch (e) { /* 存不了就算了，不影响本次显示 */ }
    }

    /** 把字号写进 :root —— 全站生效；返回实际生效的整数值 */
    function applyGlobal(px) {
        var v = clamp(px);
        var root = document.documentElement;
        root.style.setProperty('--reading-size', v + 'px');
        root.style.setProperty('--reading-scale', String(v / SCALE_BASE_PX));
        // 触屏 / 桌面所有显示"当前字号"的地方 + A−/A+ 的禁用态，统一在这里同步，
        // 免得滑块和快捷按钮各改各的、数字对不上。
        paintReadouts(v);
        paintTier(v);
        return v;
    }

    /** 大字档标记（第 6 步，2026-11-01）
     *
     *  为什么要在 <html> 上挂一个属性、而不是用媒体查询判断：
     *  **媒体查询读不到 --reading-size** —— 字号是用户在我们站里选的，
     *  只有 JS 知道。CSS 想据此改布局（两列降一列、折叠导航）就必须看这个标记。
     *
     *  为什么阈值取 30：默认档 20、中档 24 都还能塞下两列（390 宽时每格内宽约
     *  137px）；到 30 时正文已近 23px，「生成食谱」四个字要 93px + 内边距 48px，
     *  两列就装不下了。宁可早一档降级，也不要在 32 那一档挤成两行。
     */
    var XL_PX = 30;

    function paintTier(px) {
        var root = document.documentElement;
        if (px >= XL_PX) {
            root.setAttribute('data-reading', 'xl');
        } else {
            root.removeAttribute('data-reading');
            // 字号调回普通档后导航恢复常显，别留着"菜单已展开"的半开状态
            closeNav();
        }
    }

    /** 大字档的「菜单」按钮（第 6 步）：点一下摊开 / 收起导航 */
    function initNavToggle() {
        var btn = document.getElementById('nav-toggle');
        var menu = document.getElementById('nav-menu');
        if (!btn || !menu) return;
        btn.addEventListener('click', function () {
            var open = menu.classList.toggle('is-open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    }

    function closeNav() {
        var menu = document.getElementById('nav-menu');
        var btn = document.getElementById('nav-toggle');
        if (menu) menu.classList.remove('is-open');
        if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    /** 同步所有"当前字号"的显示：导航数字、A−/A+ 禁用态、设置页滑块与预览
     *
     *  ⚠️ 2026-11-01 修的 bug：这里原来只写了数字，没动滑块的 value。
     *     于是「先在设置页把滑块拖到 40，再回导航点 A−」时，导航数字变成 24、
     *     滑块却仍停在最右端 —— 同一页上两个控件互相矛盾，用户会以为坏了。
     *     **凡是"回车位/回显"的地方都必须收敛到这一个函数**，别在各自的事件里
     *     单独改自己那一块。
     */
    function paintReadouts(px) {
        var nodes = document.querySelectorAll('.js-a11y-size-now');
        for (var i = 0; i < nodes.length; i++) {
            nodes[i].textContent = String(px);
        }
        var dec = document.getElementById('a11y-size-dec');
        var inc = document.getElementById('a11y-size-inc');
        if (dec) dec.disabled = px <= MIN_PX;
        if (inc) inc.disabled = px >= MAX_PX;

        // 设置页：滑块本体（先钉死 min/max，免得模板写死的小范围把 value 夹走）
        var slider = document.getElementById('reading-size');
        if (slider) {
            slider.min = String(MIN_PX);
            slider.max = String(MAX_PX);
            if (slider.value !== String(px)) slider.value = String(px);
        }
        // 设置页：数字读数 + 「效果预览」那段文字
        var readout = document.getElementById('reading-size-value');
        if (readout) readout.textContent = String(px);
        var preview = document.getElementById('reading-preview');
        if (preview) preview.style.setProperty('--reading-size', px + 'px');
    }

    /** 读高对比偏好；返回 true / false
     *
     *  三层优先级（第 5 步，2026-11-01）：
     *    1. 用户在本站明确打开（'high'）    → 开
     *    2. 用户在本站明确关闭（'normal'）  → 关（**盖住系统设置**，要能关得掉）
     *    3. 没存过                          → 跟系统「增强对比度」走
     */
    function readContrast() {
        if (readKey(CONTRAST_KEY, '') === CONTRAST_ON) return true;
        if (!contrastFollowsSystem()) return false;
        return systemContrast();
    }

    /** 用户还没在本站选过 → 由系统偏好决定 */
    function contrastFollowsSystem() {
        var saved = readKey(CONTRAST_KEY, '');
        return saved !== CONTRAST_ON && saved !== CONTRAST_OFF;
    }

    /** 系统是否要求「增强对比度」（iOS 增强对比度 / Android 高对比度文字 / Win 对比度主题） */
    function systemContrast() {
        try {
            return !!(window.matchMedia && window.matchMedia(SYS_CONTRAST_QUERY).matches);
        } catch (e) {
            return false;   // 老浏览器没有 matchMedia
        }
    }

    /** 应用高对比 —— 写 <html data-contrast="high">，关闭时移除属性
     *
     *  ⚠️ 关的时候不能一律 removeAttribute：系统开着「增强对比度」时，
     *     style.css 里 `@media (prefers-contrast: more)` 会立刻又把它打开，
     *     用户会觉得"这个开关坏了"。所以那种情况下要留一个显式 'normal' 标记，
     *     CSS 那边用 `:not([data-contrast="normal"])` 认它。
     */
    function applyContrast(on) {
        var root = document.documentElement;
        if (on) {
            root.setAttribute('data-contrast', CONTRAST_ON);
        } else if (systemContrast()) {
            root.setAttribute('data-contrast', CONTRAST_OFF);
        } else {
            root.removeAttribute('data-contrast');
        }
        updateSysNote();
        return !!on;
    }

    /** 设置页提示条：只在「确实是系统帮我们打开的」时露面。
     *  用户自己动手开的不用解释；他关掉之后更不该再挂一行"已跟随系统"。 */
    function updateSysNote() {
        var note = document.getElementById('a11y-sys-note');
        if (!note) return;
        var applied = document.documentElement.getAttribute('data-contrast') === CONTRAST_ON;
        note.hidden = !(applied && systemContrast() && contrastFollowsSystem());
    }

    function saveContrast(on) { writeKey(CONTRAST_KEY, on ? CONTRAST_ON : CONTRAST_OFF); }

    // ---- 尽早应用（本脚本在 <head> 同步执行，先于正文渲染） ----
    applyGlobal(readKey(SIZE_KEY, DEFAULT_PX));
    applyContrast(readContrast());

    // 暴露给其它脚本 / 控制台调试用
    window.YSAReadingSize = {
        SCALE_BASE_PX: SCALE_BASE_PX,
        DEFAULT_PX: DEFAULT_PX,
        MIN_PX: MIN_PX,
        MAX_PX: MAX_PX,
        apply: applyGlobal,
        read: function () { return clamp(readKey(SIZE_KEY, DEFAULT_PX)); },
        save: function (px) { writeKey(SIZE_KEY, px); }
    };
    window.YSA11y = {
        applySize: applyGlobal,
        applyContrast: applyContrast,
        readContrast: readContrast,
        saveContrast: saveContrast,
        systemContrast: systemContrast,
        followsSystem: contrastFollowsSystem
    };

    // ---- 导航栏快捷字号（全站，含未登录的登录页） ----------------------
    // 老人不会去"设置"里找滑块，所以入口放在顶部常驻。
    // 步进 2px：16→40 一共 12 下，一下一下按也不至于手酸。
    var QUICK_STEP_PX = 2;

    function initQuickSize() {
        var dec = document.getElementById('a11y-size-dec');
        var inc = document.getElementById('a11y-size-inc');
        if (!dec && !inc) return;

        function step(delta) {
            var next = applyGlobal(readKey(SIZE_KEY, DEFAULT_PX) - 0 + delta);
            writeKey(SIZE_KEY, next);
        }

        if (dec) dec.addEventListener('click', function () { step(-QUICK_STEP_PX); });
        if (inc) inc.addEventListener('click', function () { step(+QUICK_STEP_PX); });

        // 进页面先同步一次（此时按钮已存在，禁用态要按当前值画对）
        paintReadouts(clamp(readKey(SIZE_KEY, DEFAULT_PX)));
    }

    // ---- 设置页控件（其它页面没有这些元素，直接跳过） ----
    function initSettingsPage() {
        initSizeSlider();
        initContrastSwitch();
    }

    function initAll() {
        initQuickSize();
        initSettingsPage();
        watchSystemContrast();
        initNavToggle();
        // <head> 里那次 applyContrast 跑得比正文早，提示条元素还不存在；
        // DOM 就绪后再刷一次，否则"已跟随系统设置"永远不显示。
        updateSysNote();
    }

    /** 系统偏好中途变化（用户在设置面板里现场拨开关）—— 没手动选过才跟随
     *
     *  没有这一步的话：把网页开在后台、去系统里打开「增强对比度」再回来，
     *  页面还是老样子，得刷新才生效。老人不会想到要刷新。
     */
    function watchSystemContrast() {
        if (!window.matchMedia) return;
        var mq;
        try { mq = window.matchMedia(SYS_CONTRAST_QUERY); } catch (e) { return; }

        function onChange() {
            if (!contrastFollowsSystem()) return;
            applyContrast(mq.matches);
            var box = document.getElementById('high-contrast');
            if (box) box.checked = mq.matches;
        }

        if (mq.addEventListener) mq.addEventListener('change', onChange);
        else if (mq.addListener) mq.addListener(onChange);   // Safari 13 及更早
    }

    function initSizeSlider() {
        var slider = document.getElementById('reading-size');
        if (!slider) return;

        var readout = document.getElementById('reading-size-value');
        var preview = document.getElementById('reading-preview');
        var resetBtn = document.getElementById('reading-reset');

        // 滑块的上下限跟常量走，别让模板里写死的 min/max 和 JS 打架
        slider.min = String(MIN_PX);
        slider.max = String(MAX_PX);

        var saved = clamp(readKey(SIZE_KEY, DEFAULT_PX));
        slider.value = String(saved);
        paintReadout(saved, readout);
        paintPreview(saved, preview);

        // 拖动中：只动预览区（在预览元素上写行内变量，覆盖全局值）
        slider.addEventListener('input', function () {
            var v = clamp(slider.value);
            paintReadout(v, readout);
            paintPreview(v, preview);
        });

        // 松手：全站应用 + 记住
        slider.addEventListener('change', function () {
            var v = applyGlobal(slider.value);
            writeKey(SIZE_KEY, v);
            paintReadout(v, readout);
            paintPreview(v, preview);
        });

        // 恢复默认
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                slider.value = String(DEFAULT_PX);
                applyGlobal(DEFAULT_PX);
                writeKey(SIZE_KEY, DEFAULT_PX);
                paintReadout(DEFAULT_PX, readout);
                paintPreview(DEFAULT_PX, preview);
            });
        }
    }

    function initContrastSwitch() {
        var box = document.getElementById('high-contrast');
        if (!box) return;

        box.checked = readContrast();

        // 立即生效（不像字号需要"预览"，高对比是全站判定，没有中间态）
        box.addEventListener('change', function () {
            var on = applyContrast(box.checked);
            saveContrast(on);
        });
    }

    function paintReadout(px, el) {
        if (el) el.textContent = String(px);
    }

    function paintPreview(px, el) {
        if (el) el.style.setProperty('--reading-size', px + 'px');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();
