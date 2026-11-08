# -*- coding: utf-8 -*-
# utils/checkin.py
"""每日血糖记录 —— 零迁移的「手工录入」通道

为什么要有这一层
    上传报告的口径是「一份报告 = 一批指标」，一年也就一两次；
    而血糖是**每天**的事（糖友一天可能要测 2~3 次）。用报告的口径承载每日数据，
    等于要求用户每天拍一张血糖仪的照片再上传，很蠢。

    所以这一块的产品定位就是**糖友自己的血糖本**：看的是自己这几天的升降，
    而不是"医生给我下诊断"。因此卡片上一次摆出空腹 / 餐后 1 小时 / 餐后 2 小时
    三个时点，而不是一张能填 14 项指标的大表。

    数据层其实早就留了后门：`health_metric.report_id` 是 nullable，
    注释原文就是「手工录入的指标允许为空」。所以这里**不改表、不迁移**，
    只需要一个"校验 + 落库"的收敛点，别把逻辑散在路由里。

与「上传健康报告」共存的三条铁律
（对应 docs/01-产品与方案/R5-改进方案与实施记录.md 的 2.1.1）

    1. **只碰 report_id IS NULL 的行**。
       报告写进来的指标（report_id 非空）这里一行都不读、不改、不删。
       所以"这边一填，那边报告里的血糖就没了"这件事从结构上不可能发生。

    2. **同一天同一指标重复记录 → 覆盖自己那条，不新增第二个点**。
       用户手抖填错要能改，不能变成趋势图上两个打架的点。
       （这与"不覆盖"不矛盾：不覆盖的是**报告的行**，不覆盖的是**历史的那一天**。）

    3. **"今天"按北京时间算，存储仍是 UTC**（2026-10-24 修正）。
       原文档写的是"用本地时间不用 utcnow"，代码也确实用了 `datetime.now()` ——
       但**线上容器的 TZ 是空的，`datetime.now()` 返回的就是 UTC**，
       于是这条铁律在线上并没有生效：北京时间早上 7:00 的记录会存成 UTC 前一天 23:00，
       而"今天"又按 UTC 算 → 卡片显示"今天还没记录"，用户 8:00 再打开以为数据丢了
       （清晨空腹血糖正好是这个产品最典型的场景）。
       现在统一走 `utils/biz_time`：**存储一律 UTC，判断"哪一天"一律按北京时间**。
       本地开发机是 UTC+8，所以以前本地怎么测都测不出来 —— 这也是为什么
       取时间必须用 `biz_time.utc_now()`，不能再用 `datetime.now()`。

自测
    "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" utils/checkin.py
"""
from datetime import datetime

from utils.biz_time import biz_date_str, biz_day_bounds, to_biz, utc_now
from utils.database import db
from models.health_metric import HealthMetric
from utils.metric_store import latest_metrics


# ---------------------------------------------------------------------------
# 记录支持哪几项
# ---------------------------------------------------------------------------
# 只放糖友真正天天会量的血糖时点，**共三个**。故意不做成"所有指标都能填"：
# 选项越多，中老年用户越不敢点，3 秒就变成 3 分钟。
# 其余指标（血脂、尿酸、糖化）本来就是抽血才有的，走报告上传。
#
# ⚠️ 体重**不在这份 FIELDS 里**，它在下面的 WEIGHT_FIELD（单独一行，不进这个网格）。
#   理由：四个框在手机上会换成 2+2 两行，把最常用的空腹血糖和餐后 1 小时挤到第二行。
#   完整经过（白天拿掉、当晚加回来）见 WEIGHT_FIELD 上方的注释。
#
# label  —— 落库用的名字，**必须与 report_parser 里的一模一样**
#            （报告解析出来是"餐后血糖"，这里写成别的，同一指标在趋势图上会出现两个名字）
# ui_label —— 只在卡片上显示，可以比 label 更口语、更明确
# placeholder —— 刻意留空：填数字会让人以为"系统已经帮我填好了"
FIELDS = (
    {
        'key': 'fasting_glucose', 'label': '空腹血糖', 'ui_label': '空腹血糖值',
        'unit': 'mmol/L', 'lo': 1.0, 'hi': 40.0, 'step': 0.1, 'placeholder': '',
    },
    {
        'key': 'postprandial_1h_glucose', 'label': '餐后1小时血糖',
        'ui_label': '餐后 1 小时血糖',
        'unit': 'mmol/L', 'lo': 1.0, 'hi': 45.0, 'step': 0.1, 'placeholder': '',
    },
    {
        'key': 'postprandial_glucose', 'label': '餐后血糖', 'ui_label': '餐后 2 小时血糖',
        'unit': 'mmol/L', 'lo': 1.0, 'hi': 45.0, 'step': 0.1, 'placeholder': '',
    },
)

# ---------------------------------------------------------------------------
# 体重：单独一行，不进上面那张「血糖三项」网格
# ---------------------------------------------------------------------------
# 2026-10-22 白天先从卡片拿掉，当晚又加回来 —— 但**位置不同**。
#
# 为什么加回来
#   我当初的理由是"体重是低频指标（周/月一次），走报告上传就够"。这个判断错了两处：
#   ① 报告一年才传一两次，趋势图上就两个点，等于看不出变化；
#   ② 减重 5%~10% 本身就是 2 型糖尿病管理的主要手段之一，一周称一次太正常了。
#   糖友真正想要的是"我这周比上周轻了没"，这件事报告给不了。
#
# 为什么不直接塞进 FIELDS
#   四个框在手机上会换成 2+2 两行，把最常用的空腹血糖和餐后 1 小时挤到第二行。
#   所以它单独占一行（模板里的 checkin-weight-row），血糖三项仍是一行齐。
#
# 落库口径与报告一致：key 用 'weight'、label 用 '体重'，
# 这样报告里的体重和这里手填的体重在指标区能画进同一条趋势图。
WEIGHT_FIELD = {
    'key': 'weight', 'label': '体重', 'ui_label': '体重',
    'unit': 'kg', 'lo': 20.0, 'hi': 300.0, 'step': 0.1, 'placeholder': '',
}

HEIGHT_FIELD = {
    'key': 'height', 'label': '身高', 'ui_label': '身高',
    'unit': 'cm', 'lo': 50.0, 'hi': 250.0, 'step': 0.1, 'placeholder': '',
}

# 校验 / 落库 / 回填走这一份（血糖三项 + 体重 + 身高）；
# 卡片主网格只循环 FIELDS，体重单独传字段。
ALL_FIELDS = FIELDS + (WEIGHT_FIELD, HEIGHT_FIELD)

FIELD_MAP = {f['key']: f for f in ALL_FIELDS}

# 血糖三项的 key —— 只给「今天记过没有」用，别拿它判断别的。
# ⚠️ 不能拿 FIELD_MAP（含体重）去算 done / filled_count：
#    只称了一次体重、血糖一项没测，卡片就会显示"今天已记录"，
#    用户以为血糖记过了，其实是空的（2026-10-22 发现并修）。
GLUCOSE_KEYS = tuple(f['key'] for f in FIELDS)

# 报告原文的写法会影响"这一栏到底指什么"的那几项。
# 目前只有餐后：报告可能写「餐后2小时血糖」，也可能只写「餐后血糖」（没说时点）。
# 后者若只标"报告 8.2"，会被读成"报告量的是 2 小时"，所以提示语要写全。
_LABEL_SENSITIVE_KEYS = ('postprandial_glucose',)


def _day_bounds(when=None):
    """返回 (当天 00:00, 次日 00:00)，**北京时间的"当天"**，但用 UTC 表示。

    返回值直接拿去和 `measured_at`（存的是 UTC）比较 —— 换算在 biz_time 里做完了，
    调用方不用再自己减 8 小时（那种减法错一次就是整整一天的偏移）。

    when：任意"时刻"（按 UTC 理解）；None = 现在。
    """
    return biz_day_bounds(when)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def parse_checkin(values):
    """把表单里的字符串转成待落库的条目。

    Args:
        values: dict，形如 {'fasting_glucose': '7.2', 'weight': ''}；键可以是
                表单名也可以直接是 metric_key（两者相同）

    Returns:
        (items, errors)
          items  —— [{'key','label','unit','value'}, ...]，只含**填了东西**的项
          errors —— [(key, 中文错误), ...]；有 errors 时调用方不应落库

    空值不算错（"只填一个也能存"）。填了但不是数字、或者明显超出人体范围，才算错。
    """
    items, errors = [], []

    for field in ALL_FIELDS:
        raw = values.get(field['key'])
        if raw is None:
            continue
        text = str(raw).strip()
        if text == '':
            continue

        # 中文全角句号/逗号、末尾单位（"7.2mmol/L"）都可能被粘进来，先清一遍
        cleaned = (text.replace('。', '.').replace('，', '.')
                       .replace('mmol/L', '').replace('mmol/l', '')
                       .replace('kg', '').replace('KG', '')
                       .replace('cm', '').replace('CM', '').replace('厘米', '').strip())

        try:
            number = float(cleaned)
        except (TypeError, ValueError):
            errors.append((field['key'], f"{field['label']}填的不是数字"))
            continue

        if number != number or number in (float('inf'), float('-inf')):   # NaN / inf
            errors.append((field['key'], f"{field['label']}填的不是数字"))
            continue

        if not (field['lo'] <= number <= field['hi']):
            errors.append((
                field['key'],
                f"{field['label']}超出合理范围（{_num(field['lo'])}~{_num(field['hi'])}{field['unit']}）",
            ))
            continue

        items.append({
            'key': field['key'],
            'label': field['label'],
            'unit': field['unit'],
            'value': round(number, 2),
        })

    return items, errors


def _num(value):
    return str(int(value)) if float(value) == int(value) else str(value)


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def save_checkin(user_id, items, when=None):
    """把记录条目写进 `health_metric`（report_id 留空）。

    同日同指标已有记录 → 改那条（铁律 2）；否则新增。
    **只查 report_id IS NULL 的行**，报告数据完全不参与。

    Returns:
        {'saved': 写入/更新条数, 'updated': 其中更新了几条, 'error': 出错信息或 None}
        —— 与 metric_store 一致：异常被吞掉，永不外抛
    """
    if not items:
        return {'saved': 0, 'updated': 0, 'error': None}

    start, end = _day_bounds(when)
    # 存储统一 UTC（与报告行、与 models 的 utcnow 默认值同一口径），
    # 但"算哪一天"由上面的北京窗口决定，两者不冲突。
    stamp = when or utc_now()
    saved = updated = 0

    try:
        # 只在身高/体重有更新时计算；历史补录不能借用未来的数据。
        save_items = list(items)
        supplied = {item['key']: item['value'] for item in items}
        if 'height' in supplied or 'weight' in supplied:
            body = {}
            for key in ('height', 'weight'):
                row = HealthMetric.query.filter(
                    HealthMetric.user_id == user_id,
                    HealthMetric.metric_key == key,
                    HealthMetric.measured_at <= stamp,
                ).order_by(HealthMetric.measured_at.desc(), HealthMetric.id.desc()).first()
                body[key] = supplied.get(key, row.value if row else None)
            height, weight = body['height'], body['weight']
            if height and weight and 50 <= height <= 250 and 20 <= weight <= 300:
                save_items.append({'key': 'bmi', 'label': 'BMI', 'unit': '',
                                   'value': round(weight / (height / 100) ** 2, 1)})
        for item in save_items:
            exists = HealthMetric.query.filter(
                HealthMetric.user_id == user_id,
                HealthMetric.report_id.is_(None),          # ← 铁律 1：只碰记录行
                HealthMetric.metric_key == item['key'],
                HealthMetric.measured_at >= start,
                HealthMetric.measured_at < end,
            ).order_by(HealthMetric.id.desc()).first()

            if exists:
                exists.value = item['value']
                exists.unit = item['unit']
                exists.label = item['label']
                exists.measured_at = stamp
                if item['key'] != 'bmi':
                    updated += 1
            else:
                db.session.add(HealthMetric(
                    user_id=user_id,
                    report_id=None,
                    metric_key=item['key'],
                    label=item['label'],
                    value=item['value'],
                    unit=item['unit'],
                    measured_at=stamp,
                ))
            if item['key'] != 'bmi':
                saved += 1

        db.session.commit()
        return {'saved': saved, 'updated': updated, 'error': None}
    except Exception as exc:                      # noqa: BLE001 —— 兜底，不外抛
        db.session.rollback()
        return {'saved': 0, 'updated': 0, 'error': str(exc)}


# ---------------------------------------------------------------------------
# 读：给卡片回填 + 显示"上次多少"
# ---------------------------------------------------------------------------
def today_checkin(user_id, when=None):
    """今天已经填过的记录值 → {'fasting_glucose': 7.2, ...}；没填过返回 {}。"""
    start, end = _day_bounds(when)
    rows = HealthMetric.query.filter(
        HealthMetric.user_id == user_id,
        HealthMetric.report_id.is_(None),
        HealthMetric.measured_at >= start,
        HealthMetric.measured_at < end,
    ).order_by(HealthMetric.id.asc()).all()

    values = {}
    for row in rows:
        if row.metric_key in FIELD_MAP:
            values[row.metric_key] = row.value       # 同日多条时取最后写入的
    return values


def _last_row(user_id, key, before=None, manual=True):
    """某一项最近的一条记录。manual=True 只看手填行，False 只看报告行。"""
    query = HealthMetric.query.filter(
        HealthMetric.user_id == user_id,
        HealthMetric.metric_key == key,
    )
    query = query.filter(HealthMetric.report_id.is_(None) if manual
                         else HealthMetric.report_id.isnot(None))
    if before is not None:
        query = query.filter(HealthMetric.measured_at < before)
    return query.order_by(HealthMetric.measured_at.desc(), HealthMetric.id.desc()).first()


def last_values(user_id, before=None):
    """每一项**上一次**的值，用于输入框旁边的"上次 7.2"提示。

    **手填优先，没有手填才退回报告值**（2026-10-23 加）。
    之前只取手填行，于是出现了"报告里明明有血糖、卡片却说还没有记录"的困惑：
    数据没丢，只是这句提示不去读报告。但也不能让报告值和手填值混为一谈 ——
    报告是一份快照，可能是半年前体检单上的数，冒充"昨天你量的"会误导人。
    所以退回报告值时，返回里带 source='report'，展示层据此改写成"报告 xx"。

    before：只取这个时间点**之前**的记录。调用方传"今天 00:00"，
            这样"今天已经填了 7.8"时，下面提示的是上一次（比如 7.2 · 10-18），
            而不是把今天刚填的值再说一遍。今天上传的报告同样被排除。
    """
    result = {}
    for key in FIELD_MAP:
        for source, manual in (('manual', True), ('report', False)):
            row = _last_row(user_id, key, before=before, manual=manual)
            if row is not None:
                result[key] = {
                    'value': row.value,
                    'measured_at': row.measured_at,
                    'source': source,
                    'label': row.label or '',      # 报告原文怎么叫它（见 _decorate）
                }
                break
    return result


def _decorate(field, today, last):
    """给一个字段附上「今天已填的值」和「上次多少」两样展示数据。

    血糖三项和体重共用这一份，免得模板里出现两套数字格式。

    来源是报告时不写「上次」—— 报告是快照，不是"上次你量的"。
    还有一处更细的：报告只写「餐后血糖」、没写几小时时，这一栏的标题却是
    「餐后 2 小时血糖」，若只显示「报告 8.2」会被读成"报告量的是 2 小时"。
    所以这种情形前缀写全「报告里的餐后血糖」。
    """
    item = dict(field)
    item['value'] = _num(today[field['key']]) if field['key'] in today else ''
    previous = last.get(field['key'])
    item['last_text'] = ''
    item['last_source'] = ''
    item['last_label'] = ''
    if previous:
        source = previous.get('source') or 'manual'
        label = previous.get('label') or ''
        item['last_source'] = source
        item['last_label'] = label
        if source == 'report':
            prefix = '报告'
            # 报告原文没写时点（label 里没有"小时"）→ 别让它冒充那一栏的时点
            if field['key'] in _LABEL_SENSITIVE_KEYS and label and '小时' not in label:
                prefix = '报告里的%s' % label
        else:
            prefix = '上次'
        # "上次 7.2 · 10-18" / "报告 7.2 · 06-15" / "报告里的餐后血糖 8.2 · 10-20"
        # 日期按**北京时间**显示：库里存 UTC，早上 7 点的记录是 UTC 前一天 23:00，
        # 直接 strftime 会写成"前一天"。
        item['last_text'] = '%s %s · %s' % (
            prefix,
            _num(previous['value']),
            to_biz(previous['measured_at']).strftime('%m-%d') if previous['measured_at'] else '—',
        )
    return item


def build_card_context(user_id, action_url, next_url='', when=None):
    """组装血糖记录卡片的展示数据（模板只负责画，不做任何计算）。"""
    today = today_checkin(user_id, when=when)
    start, _ = _day_bounds(when)
    # 只取"今天之前"的，避免今天填了之后下面的提示跟上面输入框重复同一句话
    last = last_values(user_id, before=start)

    # 「今天记过没有」只算血糖三项：只称了体重不该让卡片显示"今天已记录"
    glucose_filled = [k for k in today if k in GLUCOSE_KEYS]

    return {
        'fields': [_decorate(f, today, last) for f in FIELDS],   # 血糖三项（一行网格）
        'weight': _decorate(WEIGHT_FIELD, today, last),          # 体重（单独一行）
        'height': _decorate(HEIGHT_FIELD, today, last),
        'bmi': _bmi_context(user_id, when),
        'filled_count': len(glucose_filled),
        'done': bool(glucose_filled),           # 今天血糖填过没有
        'weight_filled': 'weight' in today,     # 体重单独算，模板暂未用
        'action_url': action_url,
        'next_url': next_url,
    }


def _api_item(field, today, last):
    """给客户端的一项：纯结构化字段，不含"给人看的句子"。"""
    previous = last.get(field['key']) or {}
    measured = previous.get('measured_at')
    return {
        'key': field['key'],
        'label': field['label'],            # 落库/趋势图用的名字
        'ui_label': field['ui_label'],      # 客户端可直接当输入框标签
        'unit': field['unit'],
        'lo': field['lo'],                  # 客户端可据此做前端校验
        'hi': field['hi'],
        'step': field['step'],
        'value': today.get(field['key']),   # None = 今天没填这一项
        'last_value': previous.get('value'),
        'last_date': to_biz(measured).strftime('%Y-%m-%d') if measured else None,
        # 'manual' = 自己填的；'report' = 来自上传的体检报告（快照，可能较旧）。
        # 客户端最好在 report 时把"上次"改成"报告"，别让用户以为是自己昨天量的。
        'last_source': previous.get('source'),
        # 报告原文给这一项起的名字（如「餐后血糖」或「餐后2小时血糖」）。
        # last_source=report 且这个名字不含"小时"时，客户端也把来源写全，
        # 别让「餐后血糖 8.2」在「餐后 2 小时」那一栏冒充 2 小时的值。
        'last_label': (previous.get('label') if previous.get('source') == 'report'
                       else None),
    }


def build_api_context(user_id, when=None):
    """给小程序 / 鸿蒙的**结构化**数据（`/api/v1/health/checkin` 用）。

    与 build_card_context 同源同口径 —— 都是 today_checkin + last_values 算出来的，
    区别只有一处：卡片要的是"上次 7.2 · 10-18"这种给人看的句子，
    客户端要的是 last_value / last_date 两个可自己排版的字段。

    ⚠️ 刻意不返回"给人看的中文长句"：文案一旦在服务端拼死，
       小程序想改称呼（阿姨 / 先生）就得等后端发版。
    """
    today = today_checkin(user_id, when=when)
    start, _ = _day_bounds(when)
    last = last_values(user_id, before=start)

    fields = [_api_item(f, today, last) for f in FIELDS]
    glucose_filled = [k for k in today if k in GLUCOSE_KEYS]

    return {
        # 客户端要的是"北京时间的今天"，不是服务器的日期（服务器是 UTC）
        'date': biz_date_str(when),
        'done': bool(glucose_filled),           # 只表示"血糖"记过，与卡片同口径
        'filled_count': len(glucose_filled),
        'fields': fields,                       # 血糖三项
        'weight': _api_item(WEIGHT_FIELD, today, last),   # 体重（单列，不挤进 fields）
        'height': _api_item(HEIGHT_FIELD, today, last),
        'height_filled': 'height' in today,
        'bmi': _bmi_context(user_id, when),
        'weight_filled': 'weight' in today,     # 体重今天填过没有，单独给
    }


def _bmi_context(user_id, when=None):
    row = HealthMetric.query.filter(
        HealthMetric.user_id == user_id, HealthMetric.metric_key == 'bmi',
        HealthMetric.measured_at <= (when or utc_now()),
    ).order_by(HealthMetric.measured_at.desc(), HealthMetric.id.desc()).first()
    return {'value': row.value, 'value_text': _num(row.value),
            'date': to_biz(row.measured_at).strftime('%Y-%m-%d')} if row else None


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def _self_test():
    import os
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix='checkin_test_')
    db_path = os.path.join(tmpdir, 't.db').replace('\\', '/')

    from flask import Flask
    from models.user import User                  # noqa: F401 —— 建表需要
    from models.health_report import HealthReport  # noqa: F401

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    passed = total = 0

    def check(name, cond, detail=''):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f'[PASS] {name}')
        else:
            print(f'[FAIL] {name}   {detail}')

    print('=' * 78)
    print('checkin 自测')
    print('=' * 78)

    try:
        with app.app_context():
            db.create_all()

            # 1) 校验：只填一个、空串跳过、非法值拦下
            items, errs = parse_checkin({'fasting_glucose': '7.2',
                                         'postprandial_1h_glucose': '',
                                         'postprandial_glucose': ''})
            check('只填一个也能通过', len(items) == 1 and not errs, str((items, errs)))
            check('空串不产生条目也不报错', len(errs) == 0, str(errs))

            items2, errs2 = parse_checkin({'fasting_glucose': '7.2mmol/L'})
            check('带单位也能识别', items2 and items2[0]['value'] == 7.2, str(items2))

            items3, errs3 = parse_checkin({'fasting_glucose': 'abc'})
            check('非数字被拦下', not items3 and errs3, str(errs3))

            items4, errs4 = parse_checkin({'fasting_glucose': '999'})
            check('超范围被拦下', not items4 and errs4, str(errs4))

            items5, errs5 = parse_checkin({'postprandial_glucose': '7。5'})
            check('全角句号当小数点', bool(items5) and items5[0]['value'] == 7.5, str(items5))

            # 2026-10-22：卡片改成 空腹 / 餐后1小时 / 餐后2小时 三个时点
            items6, errs6 = parse_checkin({'fasting_glucose': '6.8',
                                           'postprandial_1h_glucose': '10.2',
                                           'postprandial_glucose': '8.4'})
            check('三个时点能一次填完',
                  len(items6) == 3 and not errs6
                  and {i['key'] for i in items6} == {'fasting_glucose',
                                                     'postprandial_1h_glucose',
                                                     'postprandial_glucose'},
                  str(items6))
            check('餐后1小时的落库名字与报告解析一致',
                  any(i['key'] == 'postprandial_1h_glucose' and i['label'] == '餐后1小时血糖'
                      for i in items6), str(items6))

            # 2026-10-22 晚：体重加回来（单独一行）。校验规则与血糖同一套。
            items7, errs7 = parse_checkin({'weight': '65'})
            check('体重能单独记录',
                  len(items7) == 1 and items7[0]['key'] == 'weight'
                  and items7[0]['value'] == 65.0 and not errs7, str((items7, errs7)))

            items7b, errs7b = parse_checkin({'weight': '999'})
            check('体重超范围被拦下', not items7b and errs7b, str((items7b, errs7b)))

            items7c, errs7c = parse_checkin({'fasting_glucose': '6.8', 'weight': '72.5'})
            check('血糖和体重能一次填完',
                  len(items7c) == 2 and not errs7c, str(items7c))

            # 2) 落库 + 回填
            day = datetime(2026, 10, 20, 7, 30)
            r1 = save_checkin(1, parse_checkin({'fasting_glucose': '7.8'})[0], when=day)
            check('首次记录写入 1 条', r1['saved'] == 1 and r1['updated'] == 0, str(r1))
            check('今天能读回 7.8', today_checkin(1, when=day) == {'fasting_glucose': 7.8},
                  str(today_checkin(1, when=day)))

            # 3) 同一天再填 → 改那条，不新增
            r2 = save_checkin(1, parse_checkin({'fasting_glucose': '8.1'})[0], when=day)
            rows_same = HealthMetric.query.filter_by(user_id=1, metric_key='fasting_glucose',
                                                     report_id=None).count()
            check('同日重复记录不新增行', rows_same == 1, str(rows_same))
            check('同日重复记录更新了值', r2['updated'] == 1 and today_checkin(1, when=day)['fasting_glucose'] == 8.1,
                  str((r2, today_checkin(1, when=day))))

            # 4) 报告数据完全不受影响
            from utils.metric_store import save_metrics_for_text
            old = datetime(2026, 10, 10, 9, 0)
            save_metrics_for_text(1, '空腹血糖 6.5 mmol/L', report_id=900, measured_at=old)
            r3 = save_checkin(1, parse_checkin({'fasting_glucose': '7.9'})[0], when=day)
            report_rows = HealthMetric.query.filter_by(user_id=1, report_id=900).count()
            check('记录不动报告的 1 行', report_rows == 1, str(report_rows))
            check('报告里那条值没被改',
                  HealthMetric.query.filter_by(report_id=900).first().value == 6.5,
                  str(HealthMetric.query.filter_by(report_id=900).first().value))

            # 5) 次日记录 → 新增一条，不影响昨天
            tomorrow = datetime(2026, 10, 21, 7, 30)
            r4 = save_checkin(1, parse_checkin({'fasting_glucose': '7.1'})[0], when=tomorrow)
            total_rows = HealthMetric.query.filter_by(user_id=1, metric_key='fasting_glucose',
                                                      report_id=None).count()
            check('第二天记录新增一行', r4['saved'] == 1 and r4['updated'] == 0 and total_rows == 2,
                  str((r4, total_rows)))
            check('昨天的值还在', today_checkin(1, when=day)['fasting_glucose'] == 7.9,
                  str(today_checkin(1, when=day)))

            # 6) 排序：同一天里记录要压过"报告今天才上传"
            #    必须用独立用户，否则会被上面 10-21 那次（日期更晚）盖掉，测的就不是同一件事
            save_checkin(3, parse_checkin({'fasting_glucose': '7.9'})[0], when=day)
            save_metrics_for_text(3, '空腹血糖 6.4 mmol/L', report_id=901,
                                  measured_at=datetime(2026, 10, 20, 8, 0))
            latest3 = {c['key']: c for c in latest_metrics(3)}
            check('同一天记录优先于报告（同为 10-20）',
                  latest3['fasting_glucose']['value'] == 7.9,
                  str(latest3['fasting_glucose']))
            check('报告那条仍然留在库里',
                  HealthMetric.query.filter_by(report_id=901).first().value == 6.4,
                  str(HealthMetric.query.filter_by(report_id=901).first().value))

            # 7) 用户隔离
            check('看不到别人的记录', today_checkin(2, when=day) == {}, str(today_checkin(2, when=day)))

            # 8) 卡片上下文
            ctx = build_card_context(1, '/api/checkin/save', when=tomorrow)
            check('卡片回填今天已填的值',
                  ctx['fields'][0]['value'] == '7.1' and ctx['done'] is True,
                  str(ctx['fields'][0]))
            check('上一次的提示带上日期',
                  '上次' in ctx['fields'][0]['last_text'], str(ctx['fields'][0]['last_text']))
            check('体重是单独一项，不混进血糖三项网格',
                  len(ctx['fields']) == 3 and ctx['weight']['key'] == 'weight',
                  str([f['key'] for f in ctx['fields']]) + ' | ' + str(ctx['weight']['key']))

            # 体重落库后能回填，且和血糖互不覆盖
            save_checkin(1, parse_checkin({'weight': '72.5'})[0], when=tomorrow)
            ctx_w = build_card_context(1, '/x', when=tomorrow)
            check('体重能回填到今天输入框',
                  ctx_w['weight']['value'] == '72.5', str(ctx_w['weight']))
            check('填体重没影响血糖那三项',
                  ctx_w['fields'][0]['value'] == '7.1', str(ctx_w['fields'][0]))

            # 只称了体重、血糖一项没测 → 卡片顶部不能显示"今天已记录"
            # （2026-10-22 的 bug：done 用了含体重的 FIELD_MAP，导致血糖全空却说已记录）
            save_checkin(2, parse_checkin({'weight': '68'})[0], when=tomorrow)
            ctx_only_w = build_card_context(2, '/x', when=tomorrow)
            check('只填体重时 done 仍为 False',
                  ctx_only_w['done'] is False and ctx_only_w['filled_count'] == 0
                  and ctx_only_w['weight_filled'] is True,
                  str((ctx_only_w['done'], ctx_only_w['filled_count'],
                       ctx_only_w['weight_filled'])))
            api_only_w = build_api_context(2, when=tomorrow)
            check('接口同样：只填体重不置 done',
                  api_only_w['done'] is False and api_only_w['filled_count'] == 0
                  and api_only_w['weight_filled'] is True,
                  str((api_only_w['done'], api_only_w['filled_count'],
                       api_only_w['weight_filled'])))

            # API 通道同样是"三项 + 体重单列"
            api_ctx = build_api_context(1, when=tomorrow)
            check('接口把体重单列返回',
                  len(api_ctx['fields']) == 3
                  and api_ctx['weight']['value'] == 72.5
                  and api_ctx['weight']['unit'] == 'kg',
                  str(api_ctx['weight']))

            # 8.5) 没有手填记录时，退回上传报告里的值（2026-10-23 加）
            #      用独立用户 99，免得被上面几位的记录干扰。
            #      起因：报告里明明有血糖、卡片却说"还没有记录"，用户以为数据丢了。
            day99 = datetime(2026, 10, 23, 9, 0)
            save_metrics_for_text(99, '空腹血糖 6.4 mmol/L', report_id=902,
                                  measured_at=datetime(2026, 9, 5, 8, 0))
            last99 = last_values(99, before=datetime(2026, 10, 23, 0, 0))
            check('没有手填时退回报告值',
                  last99.get('fasting_glucose', {}).get('value') == 6.4,
                  str(last99.get('fasting_glucose')))
            check('退回时标明来源是报告',
                  last99.get('fasting_glucose', {}).get('source') == 'report',
                  str(last99.get('fasting_glucose')))

            ctx99 = build_card_context(99, '/x', when=day99)
            check('卡片写"报告 6.4"而不是"上次 6.4"',
                  ctx99['fields'][0]['last_text'].startswith('报告')
                  and '6.4' in ctx99['fields'][0]['last_text']
                  and ctx99['fields'][0]['last_source'] == 'report',
                  str(ctx99['fields'][0]['last_text']))

            # 手填之后必须改口叫"上次"，不能让报告值继续冒充
            save_checkin(99, parse_checkin({'fasting_glucose': '7.6'})[0],
                         when=datetime(2026, 10, 22, 8, 0))
            ctx99b = build_card_context(99, '/x', when=day99)
            check('有手填后改回"上次"',
                  ctx99b['fields'][0]['last_text'].startswith('上次')
                  and '7.6' in ctx99b['fields'][0]['last_text'],
                  str(ctx99b['fields'][0]['last_text']))

            # 今天刚上传的报告同样是"今天的数据"，不能当"上次"
            save_metrics_for_text(99, '餐后2小时血糖 11.8 mmol/L', report_id=903,
                                  measured_at=datetime(2026, 10, 23, 8, 30))
            ctx99c = build_card_context(99, '/x', when=day99)
            pp99 = [f for f in ctx99c['fields'] if f['key'] == 'postprandial_glucose'][0]
            check('今天上传的报告不算"上次"', pp99['last_text'] == '', str(pp99['last_text']))

            # 8.6) 报告没写时点时，别让它在「餐后 2 小时」那一栏冒充 2 小时的值
            save_metrics_for_text(99, '餐后血糖 9.6 mmol/L', report_id=904,
                                  measured_at=datetime(2026, 10, 21, 8, 0))
            ctx99d = build_card_context(99, '/x', when=day99)
            pp99d = [f for f in ctx99d['fields'] if f['key'] == 'postprandial_glucose'][0]
            check('报告只写"餐后血糖"时提示写全来源',
                  pp99d['last_text'].startswith('报告里的餐后血糖')
                  and '9.6' in pp99d['last_text'],
                  str(pp99d['last_text']))

            # 反过来：报告明确写了 2 小时，就按普通「报告」说，不必啰嗦
            save_metrics_for_text(98, '餐后2小时血糖 12.3 mmol/L', report_id=905,
                                  measured_at=datetime(2026, 10, 20, 8, 0))
            ctx98 = build_card_context(98, '/x', when=day99)
            pp98 = [f for f in ctx98['fields'] if f['key'] == 'postprandial_glucose'][0]
            check('报告写了"餐后2小时"时只说"报告 12.3"',
                  pp98['last_text'].startswith('报告 ') and '12.3' in pp98['last_text'],
                  str(pp98['last_text']))

            # 9) 空 items 不报错
            check('空提交不报错', save_checkin(1, [])['saved'] == 0, str(save_checkin(1, [])))
    finally:
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    import sys
    sys.exit(0 if _self_test() else 1)
