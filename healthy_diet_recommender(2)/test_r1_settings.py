# -*- coding: utf-8 -*-
"""R1 自测：适老化「阅读字号 + 高对比度 + 触控基线」—— 设置页与全站基础设施

覆盖：
    1. 设置页路由与渲染（滑块参数、预览区、恢复默认按钮、高对比开关）
    2. 未登录时的守卫
    3. base.html 已引入 a11y.js（且是 <head> 里的同步脚本，防闪）
    4. style.css 的字号缩放基础设施（--reading-size / --reading-scale / 6 档 token 乘倍率）
    5. style.css 的高对比模式（灰阶加深 / 主色加深 / 描边加粗 / 红黄不动）
    6. style.css 的触控基线（--tap-min 56px / --tap-gap 12px）
    7. 导航栏出现「设置」入口

用临时数据库，不碰真实 app.db。

用法
    py test_r1_settings.py
"""
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_r1_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app            # noqa: E402
from utils.database import db         # noqa: E402
from models.user import User          # noqa: E402

app = create_app()
client = app.test_client()

HERE = os.path.dirname(os.path.abspath(__file__))
CSS = os.path.join(HERE, 'static', 'css', 'style.css')
JS = os.path.join(HERE, 'static', 'js', 'a11y.js')

passed = 0
failed = 0


def check(name, cond, extra=''):
    global passed, failed
    if cond:
        passed += 1
        print('  PASS  %s' % name)
    else:
        failed += 1
        print('  FAIL  %s   %s' % (name, extra))


print('=== 1) 未登录守卫 ===')
r = client.get('/api/user/settings')
check('未登录不返回设置页', r.status_code == 401 and '阅读字号' not in r.get_data(as_text=True),
      'status=%s' % r.status_code)

print('=== 2) 登录后打开设置页 ===')
client.post('/api/user/register', data={'username': 'r1user', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'r1user', 'password': 'abc123'})
r = client.get('/api/user/settings')
html = r.get_data(as_text=True)
check('返回 200', r.status_code == 200, 'status=%s' % r.status_code)
check('页面标题正确', '设置 - 益食安康' in html)
check('有「阅读字号」标签', '阅读字号' in html)

print('=== 3) 滑块参数符合方案（16~40 / 步长 1 / 默认 20） ===')
check('是 range 控件', 'type="range"' in html)
check('min=16', 'min="16"' in html)
check('max=40', 'max="40"' in html)
check('step=1', 'step="1"' in html)
check('默认值 20', 'value="20"' in html)
check('滑块 id 正确', 'id="reading-size"' in html)

print('=== 4) 预览区与恢复默认按钮 ===')
check('有实时预览元素', 'id="reading-preview"' in html)
check('有当前值显示', 'id="reading-size-value"' in html)
check('有恢复默认按钮', 'id="reading-reset"' in html and '恢复默认' in html)
check('预览段落文案在页面里', '以下文字将按您选择的大小显示' in html)
check('不出现拖动操作的长说明（用户要求去掉）', '按住圆点左右拖动' not in html)

print('=== 5) 高对比度开关（设置页） ===')
check('有高对比开关', 'id="high-contrast"' in html)
check('是 checkbox 控件', re.search(r'id="high-contrast"[^>]*type="checkbox"', html) is not None
      or re.search(r'type="checkbox"[^>]*id="high-contrast"', html) is not None)
check('开关文案「高对比度」在页面里', '高对比度' in html)
check('开关用 <label> 包裹（整行可点）', 'a11y-switch' in html and '<label' in html)
check('开关默认不勾选（渲染期不带 checked）', 'checked' not in html.split('id="high-contrast"')[1][:120],
      html.split('id="high-contrast"')[1][:80])

print('=== 6) base.html 已引入 a11y.js（head 内同步脚本，防字号闪烁） ===')
check('页面含 a11y.js 引用', 'js/a11y.js' in html)
head = html.split('</head>')[0]
check('script 在 </head> 之前（同步执行）', 'js/a11y.js' in head)
check('script 未加 defer/async', 'defer' not in head.split('a11y.js')[1][:80]
      and 'async' not in head.split('a11y.js')[1][:80])

print('=== 7) 导航栏有「设置」入口 ===')
check('个人中心下拉出现设置', '/api/user/settings' in html)

print('=== 8) style.css 字号缩放基础设施 ===')
with open(CSS, encoding='utf-8') as f:
    css = f.read()
check('声明 --reading-size 兜底 20px（JS 失效时用）', '--reading-size: 20px;' in css)
check('声明 --reading-scale 兜底 1.1111（= 20 ÷ 锚点 18）',
      '--reading-scale: 1.1111111;' in css)
check('6 档 token 全部乘倍率', css.count('var(--reading-scale)') >= 7,
      'count=%d' % css.count('var(--reading-scale)'))
check('正文 14px 换算保留', '--fs-body: calc(14px * var(--reading-scale))' in css)
check('根字号随倍率', 'html { font-size: calc(16px * var(--reading-scale)); }' in css)
check('预览字号绑 --reading-size', '.a11y-preview' in css and 'font-size: var(--reading-size)' in css)
check('滑块触控高度 >=56px', 'height: 56px' in css)

print('=== 9) style.css 高对比模式 ===')
hc = css.split('html[data-contrast="high"]')
check('有 html[data-contrast="high"] 规则', len(hc) >= 2)
hcblock = hc[1].split('}')[0] if len(hc) >= 2 else ''
check('灰阶文字整体加深 gray-400', '--gray-400: #4b5563;' in hcblock)
check('灰阶文字整体加深 gray-500/600/700',
      all(k in hcblock for k in ('--gray-500: #374151;', '--gray-600: #1f2937;', '--gray-700: #111827;')))
check('主色按钮底色加深 green-600', '--green-600: #166534;' in hcblock)
check('描边加粗 --border-w: 2px', '--border-w: 2px;' in hcblock)
check('描边色转高对比专用色', '--border: var(--border-hc);' in hcblock)
check('红/黄语义色未被改动（风险与控糖档位必须保持醒目）',
      '--red-' not in hcblock and '--yellow-' not in hcblock)

print('=== 10) style.css 触控基线 ===')
check('声明 --tap-min: 56px', '--tap-min: 56px;' in css)
check('声明 --tap-gap: 12px', '--tap-gap: 12px;' in css)
btn = css.split('.btn {')[1].split('}')[0]
check('.btn 至少 56px 高', 'min-height: var(--tap-min)' in btn)
check('导航项至少 56px 高', re.search(r'\.nav-menu a,[\s\S]{0,200}?min-height: var\(--tap-min\)', css)
      is not None)
check('表单控件至少 56px 高', re.search(r'\.form-group select \{[^}]*min-height', css) is not None)
check('导航水平间距用 --tap-gap', '.nav-menu { display: flex; list-style: none; gap: var(--tap-gap);' in css)
check('下拉菜单项之间也有间距', 'gap: var(--tap-gap);' in css.split('.nav-dd-menu {')[1].split('}')[0])
check('全站描边已变量化（高对比才能一键加粗）',
      css.count('1px solid var(--gray-200)') == 0 and css.count('var(--border-w) solid var(--border)') >= 9,
      'gray-200 残留=%d' % css.count('1px solid var(--gray-200)'))
check('小屏顶部导航是唯一例外（48px 且有说明）',
      'min-height: 48px' in css and '48px（iOS / Android 公认的触摸最小基线）' in css)

print('=== 11) a11y.js 关键行为 ===')
with open(JS, encoding='utf-8') as f:
    js = f.read()
check('锚点 18 / 默认 20 / 范围 16~40',
      'SCALE_BASE_PX = 18' in js and 'DEFAULT_PX = 20' in js
      and 'MIN_PX = 16' in js and 'MAX_PX = 40' in js)
# 2026-11-01 踩过的坑：倍率 = 字号 ÷ DEFAULT_PX，若锚点也取 DEFAULT_PX，
# 默认档 20÷20=1，页面纹丝不动 —— 看起来像"改了没生效"。
check('倍率除的是锚点而不是默认值',
      'SCALE_BASE_PX' in js and '/ SCALE_BASE_PX' in js,
      '必须是 v / SCALE_BASE_PX，否则改默认值等于没改')
check('用 localStorage 持久化', 'localStorage' in js and 'ysak.readingSize' in js)
check('改变时不写回全局', "addEventListener('input'" in js)
check('松手后才写 :root', "addEventListener('change'" in js)
check('设置倍率 --reading-scale', '--reading-scale' in js)
# 2026-11-01 用户现场踩到的 bug：先在设置页把滑块拖到 40，再回导航点 A−，
# 导航数字变成 24、滑块却仍停在最右端 —— 同一页面上两个控件互相打架。
# 根因是「数字」「滑块」「预览」三处各改各的；现在全部收敛到 paintReadouts()。
_m = re.search(r'function paintReadouts\(px\) \{(.*?)\n    \}\n', js, re.S)
_body = _m.group(1) if _m else ''
check('回显统一收敛到 paintReadouts（滑块位置也要跟着走）',
      "getElementById('reading-size')" in _body and 'slider.value' in _body,
      '缺这条 → A−/A+ 改字号后滑块不动，用户会以为坏了')
check('脚本在加载时立即应用字号（防闪）', 'applyGlobal(readKey(SIZE_KEY, DEFAULT_PX));' in js)
check('高对比偏好也有独立存储键', 'ysak.contrast' in js)
check('高对比通过 data-contrast 生效', "setAttribute('data-contrast'" in js and "CONTRAST_ON = 'high'" in js)
check('关闭时移除属性（不留残留）', "removeAttribute('data-contrast')" in js)
check('脚本在加载时立即应用高对比（防闪）', 'applyContrast(readContrast());' in js)
check('开关绑到 #high-contrast', "getElementById('high-contrast')" in js)

print('=== 12) 档位不只靠颜色（形状符号 + 可比对的对比度） ===')


def _lum(hex_color):
    """WCAG 相对亮度"""
    h = hex_color.lstrip('#')
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _contrast(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)


check('四种指标档位都带形状符号',
      all(('.metric-tag.is-%s::before' % k) in css for k in ('ok', 'warn', 'critical', 'none')))
check('「偏高」与「需就医」符号不同（灰度/色盲也能分）',
      'content: "▲"' in css and 'content: "！"' in css,
      '两个都靠颜色的话，红绿色盲看到的是同一个色块')
check('「未判定口径」用破折号，不用对勾',
      '.metric-tag.is-none::before     { content: "—"; }' in css,
      '把"没有判定口径"画成 ✓ 等于谎称正常')

# 2026-11-01 实测：#f59e0b 橙底 + 白字 = 2.15:1，远低于 WCAG AA 的 4.5。
# 老人白内障看这种标签就是一团橙糊。这两处是「偏高」警示的唯一载体，必须能读。
check('风险提示卡「偏高」标签底色已改深（≥4.5:1）',
      re.search(r'\.risk-alert-item\.is-warn \.risk-alert-tag \{[^}]*#b45309', css) is not None
      and _contrast('#ffffff', '#b45309') >= 4.5,
      '当前白字/橙底对比度 %.2f' % _contrast('#ffffff', '#b45309'))
check('就诊卡「偏高」标签底色同上',
      re.search(r'\.visit-tag \{[^}]*background: #b45309', css, re.S) is not None
      and _contrast('#ffffff', '#b45309') >= 4.5)
for _name, _fg, _bg in (('指标卡 偏高', '#92400e', '#fef9c3'),
                        ('指标卡 正常', '#166534', '#dcfce7'),
                        ('指标卡 需就医', '#991b1b', '#fee2e2'),
                        ('需就医胶囊', '#ffffff', '#dc2626')):
    check('%s 对比度 ≥ 4.5:1' % _name, _contrast(_fg, _bg) >= 4.5,
          '实际 %.2f' % _contrast(_fg, _bg))

print('=== 13) 跟随系统设置（适老化第 5 步，2026-11-01） ===')
check('style.css 有「减弱动态效果」分支', '@media (prefers-reduced-motion: reduce)' in css)
_rm = css.split('@media (prefers-reduced-motion: reduce)')[1].split('\n@media')[0]
check('过渡与动画都压到 0.01ms（不是只改 --transition 变量）',
      'transition-duration: 0.01ms !important' in _rm
      and 'animation-duration: 0.01ms !important' in _rm,
      '只改 --transition 的话，写死 0.15s/0.4s 的那几处（tilt / toast / 折叠箭头）照样在动')
check('不是用 display:none 把元素藏掉（内容还得在）', 'display: none' not in _rm.split('scroll-behavior')[0])

check('style.css 有「增强对比度」分支', '@media (prefers-contrast: more)' in css)
_sys_hc = css.split('@media (prefers-contrast: more)')[1]
check('系统高对比带「手动关掉优先」的守卫',
      'html:not([data-contrast="normal"])' in _sys_hc,
      '没有 :not() 的话，用户在设置页关掉开关会被系统偏好立刻翻回来 —— 开关看着像坏了')


def _tokens(block):
    """取出一个 CSS 块里的自定义属性行（去掉空白，用于逐行比对）"""
    return [ln.strip() for ln in block.splitlines() if ln.strip().startswith('--')]


_manual = _tokens(css.split('html[data-contrast="high"] {')[1].split('}')[0])
_sysonly = _tokens(_sys_hc.split('html:not([data-contrast="normal"]) {')[1].split('}')[0])
check('两套高对比 token 逐行一致（改一处忘另一处会这里红）',
      _manual == _sysonly and len(_manual) >= 8,
      '手动=%d 条 / 系统=%d 条；不一致：%s' % (
          len(_manual), len(_sysonly),
          [t for t in _manual if t not in _sysonly] or [t for t in _sysonly if t not in _manual]))
check('系统高对比同样不动红/黄语义色',
      all(not t.startswith(('--red-', '--yellow-')) for t in _sysonly))

check('style.css 有「强制配色（Windows 高对比主题）」分支',
      '@media (forced-colors: active)' in css)
_fc = css.split('@media (forced-colors: active)')[1].split('\n/* 设置页')[0]
check('强制配色下把真 checkbox 放出来（自绘开关会变空白）',
      '.a11y-switch-track { display: none; }' in _fc and 'opacity: 1' in _fc)
check('强制配色下补回 outline（box-shadow 焦点框在强制色里不显示）',
      'outline: 2px solid Highlight' in _fc)
check('没有用 forced-color-adjust: none 硬保留自家配色',
      re.search(r'forced-color-adjust:\s*none\s*;', css) is None,
      '那等于把用户特意开的高对比主题关掉')

check('设置页有「已跟随系统设置」提示条', 'id="a11y-sys-note"' in html)
check('提示条默认隐藏（未开增强对比度时不该出现）',
      re.search(r'id="a11y-sys-note"[^>]*hidden', html) is not None)

check('a11y.js 查询的媒体串与样式表一致',
      "SYS_CONTRAST_QUERY = '(prefers-contrast: more)'" in js)
_rf = re.search(r'function readContrast\(\) \{(.*?)\n    \}', js, re.S)
check('readContrast 会退回系统偏好（不再只看 localStorage）',
      _rf is not None and 'systemContrast()' in _rf.group(1))
_ap = re.search(r'function applyContrast\(on\) \{(.*?)\n    \}', js, re.S)
check('关掉开关时若系统要求高对比，写显式 normal（否则关不掉）',
      _ap is not None and 'CONTRAST_OFF' in _ap.group(1) and 'systemContrast()' in _ap.group(1))
check('监听系统偏好变化（后台改完回来不用刷新）',
      "addEventListener('change', onChange)" in js)
check('系统偏好生效时有提示条，用户自己开的不提示',
      re.search(r'function updateSysNote\(\) \{(.*?)\n    \}', js, re.S) is not None
      and 'contrastFollowsSystem()' in js.split('function updateSysNote()')[1].split('\n    }')[0])
check('静态资源版本号已更新（否则用户拿旧 CSS/JS，等于没改）',
      'a11y.js?v=20261101-xl' in html and 'style.css?v=20261101-xl' in html,
      '改完 CSS/JS 不换 ?v=，浏览器缓存会继续跑旧文件')

print('=== 14) 「粗体文本」（A 方案：只在高对比模式下加粗） ===')
# 为什么不做全站默认加粗：中文的苹方/雅黑大多没有 500 中间字重（写 500 是空操作），
# 真写到 600 会让整站版面变挤，得重新验一遍所有宽度。所以只挂在高对比模式下。
_body = re.search(r'^body \{(.*?)^\}', css, re.S | re.M)
check('普通模式的 body 不带 font-weight（不误伤所有人的观感）',
      _body is not None and 'font-weight' not in _body.group(1))
check('手动高对比下正文加粗 600', 'html[data-contrast="high"] body { font-weight: 600; }' in css)
check('系统高对比下也加粗（否则只是变黑、笔画还是细的）',
      'html:not([data-contrast="normal"]) body { font-weight: 600; }' in css)
check('不写成 500（中文字体多半没有 500 字重，等于没改）',
      'font-weight: 600' in css.split('html[data-contrast="high"] body')[1][:60]
      and 'font-weight: 500' not in css.split('html[data-contrast="high"] body')[1][:60])

print('=== 15) 第 6 步：大字模式的布局降级 ===')
check('导航里有折叠按钮（aria-expanded / aria-controls 齐全）',
      'id="nav-toggle"' in html and 'aria-expanded="false"' in html
      and 'aria-controls="nav-menu"' in html)
check('导航列表有 id（按钮要指过去）', 'id="nav-menu"' in html)
check('折叠按钮平时隐藏（≤29px 不出现，不影响所有人）',
      re.search(r'\.nav-toggle \{\s*display: none', css) is not None)
check('JS 按字号挂 data-reading 标记（媒体查询读不到 --reading-size）',
      "setAttribute('data-reading', 'xl')" in js and 'XL_PX = 30' in js)
check('字号调回普通档要收起菜单（不然留个半开状态）',
      re.search(r'function paintTier\(px\) \{(.*?)\n    \}', js, re.S) is not None
      and 'closeNav()' in js.split('function paintTier(px)')[1].split('\n    }')[0])
_xl_grid = css.split('html[data-reading="xl"] .stats-grid')[1].split('}')[0]
check('两列降一列（统计卡 / 食谱卡 / 餐次卡）',
      '.recipe-cards' in _xl_grid and '.meal-cards' in _xl_grid
      and 'grid-template-columns: 1fr' in _xl_grid, _xl_grid[:120])
check('大字档下选项也改单列',
      'html[data-reading="xl"] .choice-row--even { grid-template-columns: 1fr; }' in css)
check('折叠后下拉不再做浮层（触屏上浮层最容易点错）',
      re.search(r'html\[data-reading="xl"\] \.nav-menu\.is-open \.nav-dd-menu \{[^}]*position: static', css, re.S)
      is not None)
check('降级判据是 data-reading 而不是盲目的 max-width',
      css.count('html[data-reading="xl"]') >= 8,
      'count=%d' % css.count('html[data-reading="xl"]'))

print()
print('=' * 46)
print('  通过 %d 项，失败 %d 项' % (passed, failed))
print('=' * 46)

try:
    os.remove(dbpath)
except OSError:
    pass
sys.exit(1 if failed else 0)
