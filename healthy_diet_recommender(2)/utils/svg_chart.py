# utils/svg_chart.py
"""
纯标准库 SVG 折线图 —— 给健康指标画趋势

为什么自己画
    项目约定不引入新依赖（`matplotlib` / `pandas` 会碰 NumPy，影响 YOLO 训练环境）。
    服务端直接拼出 SVG 字符串，**不依赖任何 CDN、不依赖前端图表库**，
    因此内网、离线、断网都能正常显示。

设计取舍
    - 只画折线 + 圆点 + 网格 + 坐标标签，够用即可。
    - 数值一律由调用方保证是数字；非数字项直接跳过，不报错。
    - 文字全部 `html.escape`，避免指标名里的特殊字符破坏 SVG。
    - 不画参考范围线：血脂 / 尿酸的"正常范围"属于医学口径，未确认前不擅自画。

自测
    python utils/svg_chart.py
"""
import html

# 画布与内边距（默认值，可在调用时覆盖）
DEFAULT_WIDTH = 680
DEFAULT_HEIGHT = 240
PAD_LEFT = 60
PAD_RIGHT = 18
PAD_TOP = 18
PAD_BOTTOM = 38

# 默认配色（与 static/css/style.css 的 --green-600 / 灰色系保持一致）
DEFAULT_LINE_COLOR = '#16a34a'
DEFAULT_DOT_COLOR = '#15803d'
GRID_COLOR = '#e5e7eb'
AXIS_COLOR = '#d1d5db'
TEXT_COLOR = '#6b7280'
LABEL_COLOR = '#374151'

# 横轴最多显示几个日期标签（防止挤在一起）
MAX_X_LABELS = 6
# 纵轴网格线条数
Y_GRID_LINES = 4


def _fmt(value, digits):
    """数值转字符串：去掉无意义的小数尾巴。"""
    if value is None:
        return ''
    rounded = round(float(value), digits)
    if rounded == int(rounded):
        return str(int(rounded))
    return f'{rounded:.{digits}f}'


def _axis_digits(span):
    """按数值跨度决定纵轴标签保留几位小数。"""
    if span < 2:
        return 2
    if span < 20:
        return 1
    return 0


def _normalize(points):
    """清洗输入：只保留有数字值的点，标签统一成字符串。

    字典输入里可选的 `color` 会原样带过去（柱状图用它给单根柱子换色）。
    """
    cleaned = []
    for item in points or []:
        color = None
        if isinstance(item, dict):
            raw_value = item.get('value')
            label = item.get('label', '')
            color = item.get('color')
        else:
            try:
                raw_value, label = item[0], item[1]
            except (TypeError, IndexError):
                continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value != value:            # NaN
            continue
        entry = {'value': value, 'label': str(label or '')}
        if color:
            entry['color'] = str(color)
        cleaned.append(entry)
    return cleaned


def line_chart(points, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
               unit='', line_color=DEFAULT_LINE_COLOR, dot_color=DEFAULT_DOT_COLOR,
               aria_label='指标趋势图'):
    """
    把时间序列画成一张 SVG 折线图。

    Args:
        points: [{'value': 8.9, 'label': '09-01'}, ...]，按时间正序
        unit:   纵轴单位（显示在左上角），可为空
        line_color / dot_color: 线 / 点颜色

    Returns:
        str: 完整 `<svg>` 字符串；**没有有效数据时返回空字符串**（由调用方决定显示什么）
    """
    series = _normalize(points)
    if not series:
        return ''

    values = [p['value'] for p in series]
    lo, hi = min(values), max(values)

    if hi - lo < 1e-9:
        # 所有值相同（或只有一个点）：撑开一点，避免除以零、也让点不贴在边线上
        pad = max(abs(lo) * 0.1, 1.0)
        lo, hi = lo - pad, hi + pad
    else:
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad
        if min(values) >= 0 and lo < 0:
            lo = 0.0                    # 身高体重血糖不会为负，别画出负数刻度

    span = hi - lo or 1.0
    digits = _axis_digits(span)

    plot_w = width - PAD_LEFT - PAD_RIGHT
    plot_h = height - PAD_TOP - PAD_BOTTOM
    n = len(series)

    def x_at(index):
        if n == 1:
            return PAD_LEFT + plot_w / 2.0
        return PAD_LEFT + plot_w * index / (n - 1.0)

    def y_at(value):
        return PAD_TOP + plot_h * (1.0 - (value - lo) / span)

    parts = [
        f'<svg class="metric-chart" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img" aria-label="{html.escape(aria_label)}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]

    # 纵轴单位
    if unit:
        parts.append(
            f'<text x="{PAD_LEFT}" y="12" font-size="11" fill="{TEXT_COLOR}">{html.escape(str(unit))}</text>'
        )

    # 横向网格 + 纵轴刻度
    for i in range(Y_GRID_LINES):
        ratio = i / (Y_GRID_LINES - 1.0)
        y = PAD_TOP + plot_h * ratio
        value = hi - span * ratio
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{y:.1f}" x2="{width - PAD_RIGHT}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 8}" y="{y + 4:.1f}" font-size="11" fill="{TEXT_COLOR}" '
            f'text-anchor="end">{_fmt(value, digits)}</text>'
        )

    # 坐标轴
    parts.append(
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP}" x2="{PAD_LEFT}" y2="{PAD_TOP + plot_h}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP + plot_h}" x2="{width - PAD_RIGHT}" '
        f'y2="{PAD_TOP + plot_h}" stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )

    coords = [(x_at(i), y_at(p['value'])) for i, p in enumerate(series)]

    # 折线（两个点以上才画）
    if n >= 2:
        pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in coords)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{line_color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    # 数据点
    for (x, y), point in zip(coords, series):
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{dot_color}" '
            f'stroke="#ffffff" stroke-width="1.4"><title>{html.escape(point["label"])} '
            f'{_fmt(point["value"], digits)} {html.escape(str(unit))}</title></circle>'
        )

    # 横轴日期标签：均匀抽稀，首尾必显
    step = max(1, (n + MAX_X_LABELS - 1) // MAX_X_LABELS)
    for i, point in enumerate(series):
        if not point['label']:
            continue
        if i % step and i != n - 1:
            continue
        if i == n - 1 and (n - 1) % step and n > 1:
            pass                                   # 末尾一定显示
        x = x_at(i)
        anchor = 'middle'
        if i == 0:
            anchor = 'start'
        elif i == n - 1:
            anchor = 'end'
        parts.append(
            f'<text x="{x:.1f}" y="{PAD_TOP + plot_h + 18}" font-size="11" fill="{LABEL_COLOR}" '
            f'text-anchor="{anchor}">{html.escape(point["label"])}</text>'
        )

    parts.append('</svg>')
    return ''.join(parts)


# 柱状图默认高度与配色
DEFAULT_BAR_HEIGHT = 230
DEFAULT_BAR_COLOR = '#16a34a'

# 柱子上标数值的上限：柱子太多时全标会糊成一片，就不标了
MAX_BAR_VALUE_LABELS = 24


def bar_chart(points, width=DEFAULT_WIDTH, height=DEFAULT_BAR_HEIGHT,
              unit='', bar_color=DEFAULT_BAR_COLOR, aria_label='柱状图',
              show_values=True, baseline=0.0):
    """
    把一组数值画成 SVG 柱状图（例如"每份食谱的热量"）。

    和折线图的区别
        - 折线图适合"同一个指标随时间变化"；
        - 柱状图适合"一堆并列的量互相比较"，而且**必须从 0 起**
          （柱子不从 0 起会放大差异，是典型误导）。

    Args:
        points:     [{'value': 320, 'label': '08-04'}, ...]，按要展示的顺序
        baseline:   柱子底部对应的数值，默认 0
        show_values:是否在柱顶标数值（柱子多于 MAX_BAR_VALUE_LABELS 时自动关闭）
        单个点可带 'color' 覆盖这一根的颜色

    Returns:
        str: 完整 `<svg>` 字符串；**没有有效数据时返回空字符串**
    """
    series = _normalize(points)
    if not series:
        return ''

    values = [p['value'] for p in series]
    lo = min(baseline, min(values))
    hi = max(baseline, max(values))

    if hi - lo < 1e-9:                       # 全是 0 或全同值
        hi = lo + 1.0

    top = hi + (hi - lo) * 0.18              # 顶部留出数值标签的位置
    span = top - lo or 1.0
    digits = _axis_digits(span)

    plot_w = width - PAD_LEFT - PAD_RIGHT
    plot_h = height - PAD_TOP - PAD_BOTTOM
    n = len(series)

    def y_at(value):
        return PAD_TOP + plot_h * (1.0 - (value - lo) / span)

    # 分槽：每根柱子占等宽一格，柱子居中，格子其余部分是间距
    slot = plot_w / n
    gap = min(slot * 0.28, 14.0)
    bar_w = max(slot - gap, 1.5)

    label_values = show_values and n <= MAX_BAR_VALUE_LABELS

    parts = [
        f'<svg class="metric-chart bar-chart" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img" aria-label="{html.escape(aria_label)}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]

    if unit:
        parts.append(
            f'<text x="{PAD_LEFT}" y="12" font-size="11" fill="{TEXT_COLOR}">{html.escape(str(unit))}</text>'
        )

    # 横向网格 + 纵轴刻度
    for i in range(Y_GRID_LINES):
        ratio = i / (Y_GRID_LINES - 1.0)
        y = PAD_TOP + plot_h * ratio
        value = top - span * ratio
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{y:.1f}" x2="{width - PAD_RIGHT}" y2="{y:.1f}" '
            f'stroke="{GRID_COLOR}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 8}" y="{y + 4:.1f}" font-size="11" fill="{TEXT_COLOR}" '
            f'text-anchor="end">{_fmt(value, digits)}</text>'
        )

    # 坐标轴
    parts.append(
        f'<line x1="{PAD_LEFT}" y1="{PAD_TOP}" x2="{PAD_LEFT}" y2="{PAD_TOP + plot_h}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )
    base_y = y_at(lo)
    parts.append(
        f'<line x1="{PAD_LEFT}" y1="{base_y:.1f}" x2="{width - PAD_RIGHT}" y2="{base_y:.1f}" '
        f'stroke="{AXIS_COLOR}" stroke-width="1"/>'
    )

    # 柱子
    for index, point in enumerate(series):
        x = PAD_LEFT + slot * index + gap / 2.0
        y = y_at(point['value'])
        h = base_y - y
        color = point.get('color') or bar_color
        parts.append(
            f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{max(h, 0):.1f}" rx="3" fill="{color}">'
            f'<title>{html.escape(point["label"])} {_fmt(point["value"], digits)} '
            f'{html.escape(str(unit))}</title></rect>'
        )
        if label_values and point['value']:
            parts.append(
                f'<text x="{x + bar_w / 2.0:.1f}" y="{y - 5:.1f}" font-size="10" '
                f'fill="{LABEL_COLOR}" text-anchor="middle">{_fmt(point["value"], digits)}</text>'
            )

    # 横轴标签：与折线图同一套抽稀规则（首尾必显）
    step = max(1, (n + MAX_X_LABELS - 1) // MAX_X_LABELS)
    for i, point in enumerate(series):
        if not point['label']:
            continue
        if i % step and i != n - 1 and i != 0:
            continue
        x = PAD_LEFT + slot * i + bar_w / 2.0 + gap / 2.0
        anchor = 'middle'
        if i == 0:
            anchor = 'start'
        elif i == n - 1:
            anchor = 'end'
        parts.append(
            f'<text x="{x:.1f}" y="{PAD_TOP + plot_h + 18}" font-size="11" fill="{LABEL_COLOR}" '
            f'text-anchor="{anchor}">{html.escape(point["label"])}</text>'
        )

    parts.append('</svg>')
    return ''.join(parts)


def latest_change(points):
    """
    最新一个点相对上一个点的变化方向（纯算术，不做医学判断）。

    Returns:
        dict: {"direction": "up"|"down"|"flat"|None, "delta": float|None, "pct": float|None}
    """
    series = _normalize(points)
    if len(series) < 2:
        return {'direction': None, 'delta': None, 'pct': None}

    previous, current = series[-2]['value'], series[-1]['value']
    delta = current - previous
    if abs(delta) < 1e-9:
        direction = 'flat'
    else:
        direction = 'up' if delta > 0 else 'down'
    pct = None
    if previous:
        pct = delta / previous * 100.0
    return {'direction': direction, 'delta': delta, 'pct': pct}


# ---------------------------------------------------------------------------
# 自测：直接运行本文件即可
# ---------------------------------------------------------------------------
_SAMPLE = [
    {'value': 8.9, 'label': '07-01'},
    {'value': 8.2, 'label': '07-20'},
    {'value': 7.6, 'label': '08-10'},
    {'value': 7.1, 'label': '09-01'},
]


def _coords_of(svg):
    """从 SVG 里把 polyline 的坐标抠出来，用于校验是否越界。"""
    import re
    match = re.search(r'<polyline points="([^"]+)"', svg)
    if not match:
        return []
    out = []
    for pair in match.group(1).split():
        x, y = pair.split(',')
        out.append((float(x), float(y)))
    return out


def _bars_of(svg):
    """从 SVG 里把柱子的 (x, y, width, height) 抠出来，用于校验几何。"""
    import re
    out = []
    for m in re.finditer(
            r'<rect class="bar" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"',
            svg):
        out.append(tuple(float(g) for g in m.groups()))
    return out


def _self_test():
    import re

    passed = 0
    total = 0

    def check(name, condition, detail=''):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f'[PASS] {name}')
        else:
            print(f'[FAIL] {name}   {detail}')

    print('=' * 78)
    print('svg_chart 自测')
    print('=' * 78)

    # 1) 正常多条
    svg = line_chart(_SAMPLE, unit='mmol/L')
    check('输出是 SVG', svg.startswith('<svg') and svg.endswith('</svg>'), svg[:60])
    check('包含折线', '<polyline' in svg)
    check('包含 4 个数据点', svg.count('<circle') == 4, str(svg.count('<circle')))
    check('标注了单位', 'mmol/L' in svg)

    # 2) 坐标不越界
    coords = _coords_of(svg)
    inside = all(PAD_LEFT - 0.5 <= x <= 680 - PAD_RIGHT + 0.5 and
                 PAD_TOP - 0.5 <= y <= PAD_TOP + (240 - PAD_TOP - PAD_BOTTOM) + 0.5
                 for x, y in coords)
    check('所有点落在绘图区内', inside, str(coords))

    # 3) 时间正序 → 横坐标单调递增；数值与纵坐标**反向**（y 轴向下）
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    values = [p['value'] for p in _SAMPLE]
    check('横坐标随顺序递增', xs == sorted(xs), str(xs))
    opposite = all(
        (values[i + 1] - values[i]) * (ys[i + 1] - ys[i]) < 0
        for i in range(len(values) - 1)
    )
    check('数值增大则纵坐标减小（线往上走）', opposite, f'values={values} ys={ys}')

    # 4) 单点：不画折线、不报错
    one = line_chart([{'value': 5.5, 'label': '09-01'}], unit='x')
    check('单点时只有一个圆点', one.count('<circle') == 1 and '<polyline' not in one, one[:80])

    # 5) 所有值相同：不除以零，仍出图
    same = line_chart([{'value': 5.0, 'label': 'a'}, {'value': 5.0, 'label': 'b'}])
    check('全部同值仍能出图', '<polyline' in same and 'nan' not in same.lower(), same[:80])

    # 6) 空 / 无值输入 → 空字符串
    check('空列表返回空串', line_chart([]) == '')
    check('None 返回空串', line_chart(None) == '')
    check('无有效值时返回空串', line_chart([{'value': None, 'label': 'x'}]) == '')

    # 7) 混入脏数据只跳过，不报错
    dirty = line_chart([{'value': 'abc'}, {'value': 3.2, 'label': 'ok'}, {'value': None}])
    check('脏数据被跳过', dirty.count('<circle') == 1, dirty[:80])

    # 8) 文本转义
    evil = line_chart([{'value': 1, 'label': '<script>'}], aria_label='<b>x</b>')
    check('标签被转义', '<script>' not in evil and '&lt;script&gt;' in evil, evil[:120])
    check('aria-label 被转义', '<b>x</b>' not in evil)

    # 9) 负值保护：非负数据不会画出负刻度
    nonneg = line_chart([{'value': 100, 'label': 'a'}, {'value': 101, 'label': 'b'}])
    labels = re.findall(r'>(-?\d+(?:\.\d+)?)</text>', nonneg)
    check('非负数据的纵轴不出现负数', all(not t.startswith('-') for t in labels), str(labels))

    # 10) 变化方向
    change = latest_change(_SAMPLE)
    check('变化方向为下降', change['direction'] == 'down', str(change))
    check('变化量计算正确', abs(change['delta'] - (-0.5)) < 1e-9, str(change))
    check('单点不判方向', latest_change([{'value': 1}])['direction'] is None)
    check('持平判定正确',
          latest_change([{'value': 3}, {'value': 3}])['direction'] == 'flat')

    # 11) 自定义颜色生效
    colored = line_chart(_SAMPLE, line_color='#ef4444')
    check('自定义颜色生效', 'stroke="#ef4444"' in colored)

    # ───────────────────── 柱状图 ─────────────────────
    bars_svg = bar_chart(_SAMPLE, unit='kcal')
    check('柱状图输出是 SVG', bars_svg.startswith('<svg') and bars_svg.endswith('</svg>'))
    bars = _bars_of(bars_svg)
    check('柱子数量等于数据点数量', len(bars) == 4, str(len(bars)))
    check('柱状图标了单位', 'kcal' in bars_svg)

    # 柱子都在绘图区内，且宽度为正
    inside_bars = all(
        PAD_LEFT - 0.5 <= x and x + w <= 680 - PAD_RIGHT + 0.5 and w > 0 and h >= 0
        for x, y, w, h in bars)
    check('柱子都在绘图区内且宽度为正', inside_bars, str(bars))

    # 关键：柱高与数值成正比（柱状图必须从 0 起，否则会放大差异）
    heights = [h for _, _, _, h in bars]
    ordered = [heights[i] for i in sorted(range(len(values)), key=lambda i: values[i])]
    check('柱高随数值单调递增', ordered == sorted(ordered), f'values={values} heights={heights}')
    ratio_ok = all(
        abs(heights[i] / heights[i + 1] - values[i] / values[i + 1]) < 0.02
        for i in range(len(values) - 1) if heights[i + 1] > 0)
    check('柱高比例与数值比例一致（说明从 0 起）', ratio_ok, f'values={values} heights={heights}')

    # 柱顶数值标签
    check('柱顶标了数值', '>8.9<' in bars_svg or '>8.9</text>' in bars_svg, bars_svg[-400:])

    # 单根柱子
    one_bar = _bars_of(bar_chart([{'value': 500, 'label': 'x'}], unit='kcal'))
    check('单根柱子也能画', len(one_bar) == 1 and one_bar[0][3] > 0, str(one_bar))

    # 值为 0 的柱子高度为 0，不报错、不画负高度
    zero_bar = _bars_of(bar_chart([{'value': 0, 'label': 'a'}, {'value': 100, 'label': 'b'}]))
    check('0 值柱子高度为 0', zero_bar[0][3] == 0 and zero_bar[1][3] > 0, str(zero_bar))

    # 全 0 输入不出现 NaN / 除零
    all_zero = bar_chart([{'value': 0, 'label': 'a'}, {'value': 0, 'label': 'b'}])
    check('全 0 输入不炸也不出 NaN', 'nan' not in all_zero.lower() and len(_bars_of(all_zero)) == 2)

    # 空 / 脏输入
    check('柱状图空列表返回空串', bar_chart([]) == '')
    check('柱状图 None 返回空串', bar_chart(None) == '')
    check('柱状图脏数据被跳过',
          len(_bars_of(bar_chart([{'value': 'x'}, {'value': 5, 'label': 'ok'}]))) == 1)

    # 单根柱子可覆盖颜色
    colored_bar = bar_chart([{'value': 3, 'label': 'a', 'color': '#ef4444'}])
    check('单根柱子可覆盖颜色', 'fill="#ef4444"' in colored_bar)

    # 文本转义
    evil_bar = bar_chart([{'value': 1, 'label': '<script>'}], aria_label='<b>')
    check('柱状图标签被转义', '<script>' not in evil_bar and '&lt;script&gt;' in evil_bar)

    # 柱子太多时自动不标数值（防止糊成一片）
    many = bar_chart([{'value': i + 1, 'label': str(i)} for i in range(40)])
    check('柱子过多时不标数值', len(_bars_of(many)) == 40 and 'font-size="10"' not in many,
          str(len(_bars_of(many))))

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    import sys
    sys.exit(0 if _self_test() else 1)
