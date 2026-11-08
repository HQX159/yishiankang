# routes/nutrition_routes.py
"""
网页版「营养摄入」页 —— 把已生成食谱的营养数字汇总成图和表

为什么单独一个蓝图
    原来食谱的营养信息只是一段文字，躺在详情页里，看不出"我这几天生成的都是些多高热量的菜"。
    本页是**纯展示层**：数字从 `Recipe.nutrition_info` 现场解析，**不写库、不建表、不改现有流程**。

口径（重要，页面上也会写明）
    - 这里统计的是**已生成食谱**，不是"实际吃了什么"。
      用户一天生成 7 份食谱，不代表吃了 7 顿 —— 所以本页**不做"每日摄入合计"**，
      以免把"生成了几个备选"误读成"吃进去了多少"。按份展示是刻意的。
    - 供能占比给的是**平均口径**，且参考区间是**每日**建议（碳水 50%~65%、脂肪 20%~30%、
      蛋白质 10%~15%）。单份食谱不必落在区间内，本页也不对单份做"超标"判定。
    - 不做"够不够健康"的结论。本页只把数字摆出来，判断留给医师/营养师。

访问
    GET /nutrition/intake
"""
from flask import Blueprint, render_template, session, request
import secrets
from utils.food_lookup import lookup_food
from utils.food_guide import catalog, portion_result, label_portion, CATEGORIES, FILTERS, TIPS, SOURCES, NOTICE, GI_BOUNDARY

from models.recipe import Recipe
from utils.auth import login_required
from utils.nutrition_parser import parse_nutrition, summarize
from utils.svg_chart import bar_chart

nutrition_bp = Blueprint('nutrition', __name__)


@nutrition_bp.route('/food-guide', methods=['GET', 'POST'])
@login_required
def food_guide_page():
    params = request.form if request.method == 'POST' else request.args
    query = params.get('q', '')
    category = params.get('category', 'all')
    choice = params.get('choice', 'all')
    error = ''
    result = None
    custom_result = None
    lookup_result = None
    net_offer = ''
    if not session.get('food_lookup_token'):
        session['food_lookup_token'] = secrets.token_urlsafe(24)
    try:
        foods = catalog(query, category, choice)
    except ValueError as exc:
        foods, error = [], str(exc)
    # 筛选条件把本地结果筛空了，不等于"没收录"。先退回无筛选的本地结果，
    # 这一步纯查内存常量，既快又不烧联网配额。
    if not error and query.strip() and not foods:
        try:
            fallback = catalog(query, 'all', 'all')
        except ValueError:
            fallback = []
        if fallback:
            foods, category, choice = fallback, 'all', 'all'
    # 联网必须由用户点一下才发生：整页 POST 之后要等 2~3 秒白屏，
    # 中老年用户会以为卡死；而且每 10 分钟只有 5 次额度，不能每次搜空就烧一次。
    if request.method == 'POST':
        token = request.form.get('lookup_token', '')
        if not secrets.compare_digest(token.encode('utf-8'), session['food_lookup_token'].encode('utf-8')):
            error = '页面已过期，请刷新后重新搜索'
        elif not error and query.strip() and not foods and request.form.get('net') == '1':
            try:
                lookup_result = lookup_food(query, session['user_id'])
                if lookup_result['status'] == 'local':
                    foods = lookup_result['foods']
                    query, category, choice = lookup_result['query'], 'all', 'all'
            except ValueError as exc:
                error = str(exc)
    if not error and query.strip() and not foods and lookup_result is None:
        net_offer = query
    if request.args.get('food_id'):
        try:
            result = portion_result(request.args['food_id'], request.args.get('amount'), request.args.get('label_carb'))
        except (ValueError, LookupError) as exc:
            error = str(exc)
    if request.args.get('custom_food'):
        try:
            custom_result = label_portion(request.args.get('custom_food'), request.args.get('custom_amount'),
                                          request.args.get('custom_carb'), request.args.get('custom_unit', 'g'))
        except ValueError as exc:
            error = str(exc)
    return render_template('nutrition/food_guide.html', foods=foods, q=query,
                           category=category, choice=choice, categories=CATEGORIES,
                           filters=FILTERS, tips=TIPS, sources=SOURCES, notice=NOTICE,
                           gi_boundary=GI_BOUNDARY, result=result, error=error, custom_result=custom_result,
                           lookup_result=lookup_result, net_offer=net_offer,
                           lookup_token=session['food_lookup_token'])

# 柱状图最多画多少根（太多会糊；详情列表仍然全量显示）
_MAX_BARS = 60

# 宏观营养素供能比参考区间（%）
# 碳水/脂肪取自《中国居民膳食指南（2022）》平衡膳食宝塔；蛋白质为通用参考。
# 注意这是**每日**口径，不是单份食谱的口径。
_GUIDE_RATIO = {
    'carb': (50.0, 65.0),
    'fat': (20.0, 30.0),
    'protein': (10.0, 15.0),
}

_RATIO_LABELS = {
    'carb': '碳水',
    'fat': '脂肪',
    'protein': '蛋白质',
}

# 供能占比条的配色（与站内色系一致，不涉及好坏暗示）
_RATIO_COLORS = {
    'carb': '#0ea5e9',
    'fat': '#f59e0b',
    'protein': '#16a34a',
}


def _num_text(value, digits=1):
    """7.0 → '7'，14.50 → '14.5'；None → '—'。"""
    if value is None:
        return '—'
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f'{round(number, digits)}'.rstrip('0').rstrip('.')


def _date_parts(value):
    """把 datetime 拆成页面要用的日期/时间文本。"""
    if not value:
        return '', ''
    text = value.strftime('%Y-%m-%d %H:%M')
    return text[:10], text


def _friendly_date(date_text):
    """'2026-08-08' → '2026年8月8日'。"""
    parts = (date_text or '').split('-')
    if len(parts) != 3:
        return date_text or ''
    try:
        return f'{int(parts[0])}年{int(parts[1])}月{int(parts[2])}日'
    except ValueError:
        return date_text


@nutrition_bp.route('/intake', methods=['GET'])
@login_required
def intake_page():
    user_id = session.get('user_id')

    # 按时间正序取全部食谱（要画趋势，就得从旧到新）
    recipes = Recipe.query.filter_by(user_id=user_id) \
        .order_by(Recipe.created_at.asc()).all()

    entries = []
    for recipe in recipes:
        parsed = parse_nutrition(recipe.nutrition_info)
        date_text, datetime_text = _date_parts(recipe.created_at)
        entries.append({
            'id': recipe.id,
            'title': (recipe.title or '').strip() or f'食谱 #{recipe.id}',
            'date': date_text,
            'datetime': datetime_text,
            'friendly_date': _friendly_date(date_text),
            'nutrition': parsed,
            'kcal_text': _num_text(parsed['kcal'], 1) if parsed else '—',
            'protein_text': _num_text(parsed['protein'], 1) if parsed else '—',
            'carb_text': _num_text(parsed['carb'], 1) if parsed else '—',
            'fat_text': _num_text(parsed['fat'], 1) if parsed else '—',
        })

    summary = summarize([e['nutrition'] for e in entries])

    # 柱状图：一份食谱一根柱子，不按天合并（见文件头口径说明）
    chart_entries = [e for e in entries if e['nutrition']][-_MAX_BARS:]
    chart = bar_chart(
        [{'value': e['nutrition']['kcal'],
          'label': e['date'][5:] or e['date']} for e in chart_entries],
        unit='kcal',
        aria_label='每份食谱的热量',
    ) if chart_entries else ''

    # 按日期分组，供详情列表分节显示（保持时间正序 → 最新的排在最后，页面再倒过来）
    groups = []
    for entry in entries:
        if groups and groups[-1]['date'] == entry['date']:
            groups[-1]['items'].append(entry)
        else:
            groups.append({'date': entry['date'],
                           'friendly_date': entry['friendly_date'],
                           'items': [entry]})
    groups.reverse()

    # 供能占比：把平均值换算成占比条（带参考区间的位置）
    ratio_rows = []
    ratio = summary.get('energy_ratio')
    if ratio:
        for key in ('carb', 'protein', 'fat'):
            low, high = _GUIDE_RATIO[key]
            ratio_rows.append({
                'key': key,
                'label': _RATIO_LABELS[key],
                'value': ratio[key],
                'value_text': f'{ratio[key]:.1f}%',
                'color': _RATIO_COLORS[key],
                'guide_low': low,
                'guide_high': high,
                'guide_text': f'{low:.0f}%~{high:.0f}%',
                'bar_left': min(max(ratio[key], 0.0), 100.0),
                'band_left': low,
                'band_width': high - low,
            })

    span_text = ''
    if entries:
        first, last = entries[0]['date'], entries[-1]['date']
        span_text = first if first == last else f'{first} ~ {last}'

    # 逐份里最高的那份热量（只报数，不下结论）
    peak = max((e['nutrition']['kcal'] for e in entries if e['nutrition']), default=None)

    return render_template(
        'nutrition/intake.html',
        entries=entries,
        groups=groups,
        summary=summary,
        chart=chart,
        chart_count=len(chart_entries),
        ratio_rows=ratio_rows,
        span_text=span_text,
        peak_text=_num_text(peak, 1),
        parsed_count=summary['parsed'],
        total_count=summary['total'],
        unparsed=[e for e in entries if not e['nutrition']],
        avg_kcal_text=_num_text(summary['avg']['kcal'], 1),
        avg_protein_text=_num_text(summary['avg']['protein'], 1),
    )
