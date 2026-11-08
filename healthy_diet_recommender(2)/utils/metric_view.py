# -*- coding: utf-8 -*-
# utils/metric_view.py
"""健康指标的"展示数据"组装层

为什么单独抽出来
    「健康指标」独立页（`/health/metrics`）和「上传健康报告」页**上传成功后**的指标区，
    要展示的是一模一样的东西。如果两处各写一份，阈值标签、日期格式、趋势文案迟早会各说各话。
    所以视图数据的组装只写这一处，两个路由共用，模板也共用同一个片段
    （`templates/health/_metrics_panel.html`）。

不含任何 Flask 依赖（不 import request / render_template），因此可以在自测里直接调用。

返回结构（键名刻意避开 dict 自带方法，否则 Jinja 里 `metrics.items` 会拿到方法而不是列表）
    cards         指标卡片列表（每项含 label/value/unit/status/value_text/date_text/points）
    selected      当前选中画趋势的指标 key
    selected_item 该 key 对应的卡片（找不到为 None）
    trend         metric_trend() 的原始返回（含 label/unit/points）
    chart         服务端生成的 SVG 字符串（无数据时为 ''）
    point_count   趋势点数
    change_text   与上次对比的文案（无对比时为空串）
    summary       level_summary() 的返回：{'counts': {...}, 'total': N, 'text': '共 N 项指标 · …'}
                  —— 一行"整体怎么样"的计数概览，不含任何新的医学判断

用法
    from utils.metric_view import build_metrics_context
    ctx = build_metrics_context(session.get('user_id'))
    render_template('xxx.html', metrics=ctx)

自测
    python -m utils.metric_view      （用系统 Python 3.14）
"""
from utils.metric_store import latest_metrics, metric_trend
from utils.risk_guard import evaluate_metric
from utils.svg_chart import line_chart, latest_change

# 默认优先画哪个（血糖最直观），都没有就退到列表第一个
PREFERRED_KEYS = ('fasting_glucose', 'hba1c', 'systolic_bp', 'total_cholesterol', 'bmi')

# 趋势图最多取多少个点
MAX_POINTS = 60

# 判档级别 → 卡片标签文案
LEVEL_TAGS = {
    'ok': '未见偏高',
    'warn': '偏高',
    'critical': '需就医',
}


def short_date(iso_text, with_year=False):
    """'2026-09-01T08:00:00' → '09-01'（或 '2026-09-01'）。取不到就原样返回。"""
    text = str(iso_text or '')
    if len(text) < 10:
        return ''
    parts = text[:10].split('-')
    if len(parts) != 3:
        return text[:10]
    return '-'.join(parts) if with_year else f'{parts[1]}-{parts[2]}'


def num_text(value):
    """去掉多余的 .0：172.0 → 172，7.85 → 7.85。"""
    if value is None:
        return ''
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f'{round(number, 2)}'.rstrip('0').rstrip('.')


def level_summary(cards):
    """把一组指标卡片按判档级别数一遍，给出一句话概览。

    为什么要单独给一句话
        卡片一张张摆出来，用户得自己一项项看标签才知道"整体怎么样"。
        这里只做**计数**，不产生任何新的医学判断 —— 判档结果全部来自
        `evaluate_metric()`，与拦截逻辑共用同一份阈值。
        判不出级别的（血脂、尿酸、BMI）计入 unknown，且措辞用"未判定口径"，
        不写成"正常"，避免给出未经确认的医学结论。
    """
    counts = {'critical': 0, 'warn': 0, 'ok': 0, 'unknown': 0}
    for card in cards:
        status = card.get('status') or {}
        level = status.get('level')
        counts[level if level in ('critical', 'warn', 'ok') else 'unknown'] += 1

    total = len(cards)
    if not total:
        return {'counts': counts, 'total': 0, 'text': ''}

    parts = [f'共 {total} 项指标']
    if counts['critical']:
        parts.append(f"{counts['critical']} 项需就医")
    if counts['warn']:
        parts.append(f"{counts['warn']} 项偏高")
    if counts['ok']:
        parts.append(f"{counts['ok']} 项未见偏高")
    if counts['unknown']:
        parts.append(f"{counts['unknown']} 项未判定口径")
    return {'counts': counts, 'total': total, 'text': ' · '.join(parts)}


def build_metrics_context(user_id, selected_key=None, max_points=MAX_POINTS):
    """组装指标展示数据。用户没有任何指标时返回 cards=[] 的空结构（不抛异常）。"""
    cards = latest_metrics(user_id)

    # 每一项附上判档结果与展示用文本（判不了口径的 status 为 None）
    for card in cards:
        status = evaluate_metric(card.get('key'), card.get('value'), card.get('unit'))
        if status:
            status['tag'] = LEVEL_TAGS.get(status['level'], '')
        card['status'] = status
        card['value_text'] = num_text(card.get('value'))
        card['date_text'] = short_date(card.get('measured_at'))

        # R5：标出这个数字是"自己每天填的"还是"医院报告读出来的"。
        # 判据就是 report_id —— 记录写入时留空，报告写入时必填（见 utils/checkin.py）。
        # 不标的话，用户会拿体检报告里三个月前的血糖和自己昨天填的混着看。
        card['source'] = 'report' if card.get('report_id') else 'checkin'
        card['source_text'] = '报告' if card['source'] == 'report' else '记录'

    # 选一个要画趋势的指标：指定的合法就用它，否则按偏好挑，最后退到第一个
    available = [card['key'] for card in cards]
    selected = str(selected_key or '').strip()
    if selected not in available:
        selected = next((k for k in PREFERRED_KEYS if k in available),
                        available[0] if available else '')

    trend = metric_trend(user_id, selected, limit=max_points) if selected else None
    raw_points = (trend or {}).get('points') or []
    points = [
        {'value': p.get('value'), 'label': short_date(p.get('measured_at'))}
        for p in raw_points
    ]
    trend_unit = (trend or {}).get('unit', '')
    trend_label = (trend or {}).get('label', '')
    chart = line_chart(
        points,
        unit=trend_unit,
        aria_label=f'{trend_label} 趋势图',
    ) if points else ''

    change = latest_change(points)
    change_text = ''
    if change['direction'] == 'flat':
        change_text = '与上次持平'
    elif change['direction']:
        # 注意：这是纯算术的升降，**不按好坏着色**（例如高密度脂蛋白升高是好事）
        arrow = '↑' if change['direction'] == 'up' else '↓'
        change_text = f"较上次 {arrow}{num_text(abs(change['delta']))}"
        unit_gap = '' if trend_unit in ('%', '％') else ' '
        if trend_unit:
            change_text += f'{unit_gap}{trend_unit}'
        if change['pct'] is not None:
            change_text += f"（{abs(change['pct']):.1f}%）"

    return {
        'cards': cards,
        'selected': selected,
        'selected_item': next((c for c in cards if c['key'] == selected), None),
        'trend': trend,
        'chart': chart,
        'point_count': len(points),
        'change_text': change_text,
        'summary': level_summary(cards),
    }


if __name__ == '__main__':
    # 只自测两个纯函数（其余依赖数据库，由页面端到端自测覆盖）
    import sys

    cases = [
        (short_date('2026-09-01T08:00:00'), '09-01'),
        (short_date('2026-09-01T08:00:00', with_year=True), '2026-09-01'),
        (short_date('2026-09-01'), '09-01'),
        (short_date(''), ''),
        (short_date(None), ''),
        (short_date('乱码'), ''),
        (num_text(172.0), '172'),
        (num_text('172.00'), '172'),
        (num_text(7.85), '7.85'),
        (num_text(5.900), '5.9'),
        (num_text(0), '0'),
        (num_text(None), ''),
        (num_text('—'), '—'),
    ]
    passed = 0
    for got, want in cases:
        ok = got == want
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {got!r} == {want!r}")

    # level_summary：只做计数，判不出级别的必须归到 unknown（不能算"正常"）
    def _card(level):
        return {'status': {'level': level} if level else None}

    summary_cases = [
        (
            level_summary([]),
            {'counts': {'critical': 0, 'warn': 0, 'ok': 0, 'unknown': 0}, 'total': 0, 'text': ''},
        ),
        (
            level_summary([_card('critical')]),
            {'counts': {'critical': 1, 'warn': 0, 'ok': 0, 'unknown': 0},
             'total': 1, 'text': '共 1 项指标 · 1 项需就医'},
        ),
        (
            level_summary([_card('warn'), _card('warn'), _card(None), _card(None)]),
            {'counts': {'critical': 0, 'warn': 2, 'ok': 0, 'unknown': 2},
             'total': 4, 'text': '共 4 项指标 · 2 项偏高 · 2 项未判定口径'},
        ),
        (
            level_summary([_card('critical'), _card('warn'), _card('ok'), _card(None)]),
            {'counts': {'critical': 1, 'warn': 1, 'ok': 1, 'unknown': 1},
             'total': 4, 'text': '共 4 项指标 · 1 项需就医 · 1 项偏高 · 1 项未见偏高 · 1 项未判定口径'},
        ),
    ]
    for got, want in summary_cases:
        ok = got == want
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] summary {got.get('text')!r}")
        if not ok:
            print(f'        want {want}')

    total = len(cases) + len(summary_cases)
    print(f'通过 {passed}/{total}')
    sys.exit(0 if passed == total else 1)
