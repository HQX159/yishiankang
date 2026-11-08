# -*- coding: utf-8 -*-
# utils/visit_advice.py
"""就诊沟通清单 —— 指标偏高时「去了医院该怎么说」

为什么不是「推荐吃什么药」
    docs/01-产品与方案/R5-改进方案与实施记录.md 第三节已经写清楚：
    面向公众推荐处方药，在国内属于医疗建议 / 药品推荐范畴。团队不是医疗机构、
    没有互联网诊疗资质；写错了是**用药事故**，不是产品缺陷，免责声明确实救不了。
    所以本模块产出的是「**就诊沟通清单**」——三件套：

        ① 就医提醒      —— 这个数值意味着什么、要不要尽快去
        ② 带得走的记录  —— 去之前准备什么、把哪些数据带给医生
        ③ 能问的问题    —— 三个可以照着念的问题，帮用户把门诊 3 分钟用满

    外加一块**类别级**科普（医生可能提到的药物类别、大致怎么起作用、常见注意点），
    只到「类别」，不含任何药名 / 剂量 / 「你应该吃」。

硬约束（改代码前先读，这几条是这次功能能不能上线的前提）
    1. **不写药名、不写剂量、不写「建议服用」。** 只写「双胍类」这类类别词。
       自测里有专门的断言扫全文，出现药名或 mg 就判失败。
    2. **免责声明与内容同屏可见，绝不折叠进 `<details>`。**
       模板里它排在卡片正文最后、`<details>` 之外（与「血糖提示不许折叠」同一条红线）。
    3. **纯标准库 + 纯模板文案，不调大模型。**
       就医提示这种文字一旦让模型自由发挥，就可能编出"你这个情况要吃某药"。
    4. 阈值**只从 `utils.risk_guard` 取**，不在这里另写一份 ——
       否则「卡片说偏高、拦截说正常」这种自相矛盾迟早出现。

与其它模块的分工
    risk_guard.check_health_risk  = 扫报告原文，决定"这次要不要拦"
    risk_guard.evaluate_metric    = 给库里单个数值判档（标红 / 标黄）
    visit_advice                  = 把"判档结果 + 最近记录"翻成一页能拿去门诊的话

自测
    "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" -m utils.visit_advice
"""
from datetime import datetime, timedelta

from models.health_metric import HealthMetric
from models.health_report import HealthReport
from utils.biz_time import biz_date, biz_date_str, to_biz, to_utc, utc_now
from utils.risk_guard import (
    LEVEL_OK, LEVEL_WARN, LEVEL_CRITICAL, LEVEL_ORDER,
    evaluate_metric, escalate_level,
)

# 就医场景专用的免责声明。比通用 DISCLAIMER 多两句 ——
# 一句否掉"这是处方"，一句给出低血糖急症的处置口径（糖友最可能的急症）。
VISIT_DISCLAIMER = (
    "以上内容仅用于帮助你与医生沟通，不是处方，也不是用药建议。"
    "任何药物都必须由医生根据你的检查结果开具，请勿自行购药或调整用量。"
    "如出现心慌、出冷汗、意识模糊等情况，请立即就医或拨打 120。"
)

# 血糖类指标：判断"连续偏高"只看这几个，血压/糖化不按天算
# `unspecified_glucose`（只写「血糖 9.2」那种）也算 —— 那同样是血糖记录，
# 漏了它，"连续三天偏高"就会因为报告没写明时点而数不出来。
# `postprandial_1h_glucose`（2026-10-22 新增的餐后 1 小时）同理：它也是当天的一次血糖。
GLUCOSE_KEYS = ('fasting_glucose', 'postprandial_1h_glucose', 'postprandial_glucose',
                'random_glucose', 'unspecified_glucose')

# 「连续偏高」窗口：往回看多少天、连着几天算连续
WINDOW_DAYS = 14
STREAK_DAYS = 3

# 逐条记录最多列几条（卡片上够看就行，全量在趋势图里）
MAX_ROWS = 10

# 问医生的问题：按指标生成前半句，后面不足三个用通用问题补
_QUESTION_TPL = {
    'glucose': '我最近{label}在 {value} {unit} 左右，按我现在的情况，降糖药需要调整吗？',
    'hba1c': '我的糖化血红蛋白是 {value}%，是不是说明近三个月控制得不好？需要怎么调整？',
    'bp': '我的血压是 {value} {unit}，现在的降压药需要调整吗？平时还要注意哪些？',
}

_FIXED_QUESTIONS = (
    '除了吃药，饮食上我最该先改的是哪一件？',
    '下次复查要查哪几项？多久复查一次比较合适？',
    '我这种情况要注意低血糖吗？出现心慌、出冷汗时该怎么处理？',
)

# ---------------------------------------------------------------------------
# ③ 类别级科普：**只写类别**，不写药名、不写剂量
# ---------------------------------------------------------------------------
# 来源：《中国2型糖尿病防治指南（2020年版）》第二章「药物治疗」的类别划分。
# 这里刻意只保留"类别 / 大致怎么起作用 / 常见注意点"三列 ——
# 任何一句"你应该吃"都不能出现，判断权整句交回给医生。
DRUG_CLASSES = (
    {
        'name': '双胍类',
        'how': '最常用的一线口服药，主要让肝脏少产生葡萄糖、让身体对胰岛素更敏感。',
        'note': '常见胃肠道不适；肾功能不好时医生会调整或换用别的类别。',
    },
    {
        'name': '磺脲类 / 格列奈类',
        'how': '促使胰腺多分泌胰岛素，降糖力度较强。',
        'note': '低血糖风险相对高，医生通常会叮嘱按时吃饭、随身带点糖。',
    },
    {
        'name': 'α-糖苷酶抑制剂',
        'how': '延缓主食里的碳水化合物被吸收，主要降餐后血糖。',
        'note': '容易腹胀、排气增多，一般从很小量开始慢慢加。',
    },
    {
        'name': 'DPP-4 抑制剂',
        'how': '让体内自身的 GLP-1 作用更久，血糖高时才促进胰岛素分泌。',
        'note': '单独使用低血糖风险较小；肾功能不全时部分品种需要调整。',
    },
    {
        'name': 'SGLT2 抑制剂',
        'how': '让多余的葡萄糖随尿液排出，同时有降压、减重的作用。',
        'note': '要注意泌尿生殖道感染和脱水，医生会提醒多喝水、注意清洁。',
    },
    {
        'name': 'GLP-1 受体激动剂',
        'how': '注射给药，降糖的同时常伴有体重下降。',
        'note': '常见恶心、食欲下降，需要在医生指导下逐步加量。',
    },
    {
        'name': '胰岛素',
        'how': '直接补充胰岛素，降糖力度最强；口服药控制不理想或急性期常用。',
        'note': '低血糖风险和注射方法是要点，需要医生教会你怎么监测和调整。',
    },
)

DRUG_CLASSES_LEAD = (
    '下面只是医生可能向你提到的药物类别，用来帮你听懂门诊对话，不是给你的用药建议。'
    '具体用哪一类、用不用、用多少，全部由医生根据你的检查结果决定。'
)


def _num_text(value):
    """7.80 → 7.8，16.0 → 16。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return ('%.2f' % number).rstrip('0').rstrip('.')


# ---------------------------------------------------------------------------
# ① 判档：从库里取最新值，逐项过 evaluate_metric
# ---------------------------------------------------------------------------
def collect_findings(user_id):
    """返回 (findings, level)。

    findings 每项：{'key','label','value_text','unit','level','message'}
    只收**判得出档**的指标（血脂、尿酸、BMI 没有确认口径，不参与，
    免得"未判定口径"被写成'正常'或'偏高'两种错话）。
    """
    if not user_id:
        return [], LEVEL_OK

    # 查询放在函数内 import 依赖的模型已经在模块顶部，这里直接查库。
    rows = HealthMetric.query.filter_by(user_id=user_id) \
        .order_by(HealthMetric.measured_at.asc(), HealthMetric.id.asc()).all()

    latest = {}
    for row in rows:
        if row.metric_key not in latest:
            latest[row.metric_key] = row
        else:
            old = latest[row.metric_key]
            # 同日记录优先（与 metric_store.latest_metrics 同一口径），
            # 别处改了规则这里不跟，就会出现"指标卡显示 7.8、清单说 6.5"。
            old_day = biz_date(old.measured_at)
            new_day = biz_date(row.measured_at)
            if new_day and old_day and new_day == old_day:
                if old.report_id is not None and row.report_id is None:
                    latest[row.metric_key] = row
                    continue
            latest[row.metric_key] = row

    findings, level = [], LEVEL_OK
    for key, row in latest.items():
        status = evaluate_metric(key, row.value, row.unit)
        if not status or status['level'] == LEVEL_OK:
            continue
        findings.append({
            'key': key,
            'label': status['label'],
            'value_text': _num_text(status['value']),
            'unit': status['unit'],
            'level': status['level'],
            'message': status['message'],
        })
        if LEVEL_ORDER[status['level']] > LEVEL_ORDER[level]:
            level = status['level']

    # 严重的排前面，同级按血糖 → 糖化 → 血压的自然顺序
    findings.sort(key=lambda f: (-LEVEL_ORDER[f['level']], f['key']))
    return findings, level


# ---------------------------------------------------------------------------
# ② 记录：近 N 天的血糖，用来判断"连续偏高"、也用来告诉用户"带什么去"
# ---------------------------------------------------------------------------
def collect_records(user_id, days=WINDOW_DAYS, now=None):
    """近 `days` 天的血糖记录汇总。

    Returns:
        {'days','count','high_count','rows','streak','summary_text','has_report'}
        rows 每项：{'date','label','value_text','unit','above','source'}
    """
    empty = {
        'days': days, 'count': 0, 'high_count': 0, 'rows': [],
        'streak': 0, 'summary_text': '', 'has_report': False,
    }
    if not user_id:
        return empty

    today = now if now is not None else utc_now()
    # "近 N 天"按**北京时间**算（见 utils/biz_time 的说明）：
    # 窗口起点 = 北京"今天往前 days-1 天"的 00:00，再换算成 UTC 去和 measured_at 比。
    biz_today = to_biz(today)
    start_biz = (biz_today - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start = to_utc(start_biz)

    rows = HealthMetric.query.filter(
        HealthMetric.user_id == user_id,
        HealthMetric.metric_key.in_(GLUCOSE_KEYS),
        HealthMetric.measured_at >= start,
    ).order_by(HealthMetric.measured_at.asc(), HealthMetric.id.asc()).all()

    items = []
    for row in rows:
        status = evaluate_metric(row.metric_key, row.value, row.unit)
        if not status:
            continue
        items.append({
            'when': row.measured_at or today,
            'label': status['label'],
            'value_text': _num_text(status['value']),
            'unit': status['unit'],
            'above': status['level'] != LEVEL_OK,
            'source': '记录' if row.report_id is None else '报告',
        })

    # 按天归并：一天里只要有一次偏高，这天就算"偏高"
    # "一天"指**北京的一天**（库里存 UTC，早上 7 点的记录是 UTC 前一天 23:00）
    by_day = {}
    for item in items:
        day = biz_date(item['when'])
        by_day.setdefault(day, {'any_high': False, 'count': 0})
        by_day[day]['any_high'] = by_day[day]['any_high'] or item['above']
        by_day[day]['count'] += 1

    # 连续偏高：先把"最近一个偏高的日子"找出来，再从那天往回数。
    #   - 那个日子必须是**今天或昨天**。否则"上个月连续 5 天偏高"会被当成现在的状态，
    #     白白吓人（下一条自测专门盯着这个）。
    #   - 从"最近一个偏高日"起数、而不是从今天起数：今天刚测回正常、昨天却还是 8.4 的
    #     情况很常见，这同样是"最近几天连着偏高"，该提示。
    streak = 0
    high_days = sorted(d for d, v in by_day.items() if v['any_high'])
    if high_days:
        last_high = high_days[-1]
        if (biz_today.date() - last_high).days <= 1:
            cursor = last_high
            while by_day.get(cursor, {}).get('any_high'):
                streak += 1
                cursor -= timedelta(days=1)

    high_count = sum(1 for i in items if i['above'])
    if items:
        summary = '近 %d 天记录了 %d 次血糖，其中 %d 次高于目标范围。' % (
            days, len(items), high_count)
    else:
        summary = '近 %d 天还没有血糖记录。' % days

    shown = list(reversed(items))[:MAX_ROWS]
    return {
        'days': days,
        'count': len(items),
        'high_count': high_count,
        'rows': [{
            'date': biz_date_str(i['when'], '%m-%d'),
            'label': i['label'],
            'value_text': i['value_text'],
            'unit': i['unit'],
            'above': i['above'],
            'source': i['source'],
        } for i in shown],
        'streak': streak,
        'summary_text': summary,
        'has_report': HealthReport.query.filter_by(user_id=user_id).count() > 0,
    }


# ---------------------------------------------------------------------------
# ③ 三个问题：按指标生成，不足用通用问题补齐
# ---------------------------------------------------------------------------
def build_questions(findings, limit=3):
    """生成**最多 limit 个**（默认 3 个）可以直接照着问医生的问题。

    只陈述用户自己的数值，不预设结论 —— 句式统一是"我这样，需要调整吗"，
    而不是"我该吃某某药吗"。
    """
    pool = []
    seen_kinds = set()
    for finding in findings or []:
        key = finding['key']
        if key in GLUCOSE_KEYS:
            if 'glucose' in seen_kinds:
                continue
            seen_kinds.add('glucose')
            pool.append(_QUESTION_TPL['glucose'].format(
                label=finding['label'], value=finding['value_text'], unit=finding['unit']))
        elif key == 'hba1c':
            if 'hba1c' in seen_kinds:
                continue
            seen_kinds.add('hba1c')
            pool.append(_QUESTION_TPL['hba1c'].format(value=finding['value_text']))
        elif key in ('systolic_bp', 'diastolic_bp'):
            if 'bp' in seen_kinds:
                continue
            seen_kinds.add('bp')
            pool.append(_QUESTION_TPL['bp'].format(
                value=finding['value_text'], unit=finding['unit']))

    for fixed in _FIXED_QUESTIONS:
        if len(pool) >= limit:
            break
        if fixed not in pool:
            pool.append(fixed)

    return pool[:limit]


# ---------------------------------------------------------------------------
# ④ 就诊准备清单
# ---------------------------------------------------------------------------
def build_prep(records, findings):
    """去之前要准备什么。前两条永远与"带数据"有关，后面按情况加减。"""
    items = []

    if records['count'] >= 3:
        items.append({
            'title': '把近 %d 天的血糖记录带给医生看' % records['days'],
            'detail': '%s手机上打开「健康报告」页，医生可以直接看趋势图。'
                      '记满的天数比单次化验更能说明现在的方案管不管用。'
                      % records['summary_text'],
        })
    else:
        items.append({
            'title': '先补几天血糖记录再去',
            'detail': '%s建议从今天起连续记 3 天（早上空腹一次、饭后 2 小时一次），'
                      '有了真实数据医生才好判断，比空手去更有用。' % records['summary_text'],
        })

    if records['has_report']:
        items.append({
            'title': '带上最近那份体检报告',
            'detail': '纸质单或手机里的 PDF 都行，医生要看的是化验单上的原始数值和参考范围。',
        })
    else:
        items.append({
            'title': '这次多半要重新开化验单',
            'detail': '如果三个月内没做过化验，先问清要不要空腹去，免得白跑一趟。',
        })

    items.append({
        'title': '把正在吃的药全带上',
        'detail': '包括降糖药、降压药、保健品和中药，拍照或列个单子都行。'
                  '就诊前不要自行停药或加量 —— 医生需要看到你真实的用药情况。',
    })
    items.append({
        'title': '出门前打个电话问门诊',
        'detail': '这次要不要空腹、当天早上的药吃不吃，各医院要求不一样，问一句最省事。',
    })
    return items


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------
def _title_and_lead(level, records, findings):
    """卡片的标题与首句。三档语气不同，但都不下诊断。"""
    streak = records['streak']
    if level == LEVEL_CRITICAL:
        return (
            '这个数值需要尽快就医',
            '记录里已有指标达到需要及时处理的水平。请尽快到<b>内分泌科</b>'
            '（血压高也可以到<b>心血管内科</b>）就诊，由医生确定治疗方案；'
            '<b>在这之前不要自行加药、减药或停药</b>，也不要靠饿肚子把数字压下来。',
        )
    if streak >= STREAK_DAYS:
        return (
            '最近 %d 天血糖连续偏高' % streak,
            '单次偏高不用慌，但最近连着 %d 天的记录都高于目标范围，说明现在的方案可能需要调整。'
            '建议本周内去门诊复查一次，把下面的记录带给医生看。' % streak,
        )
    return (
        '有指标偏高，建议近期复查',
        '这个数值高于常用的控制目标。一次偏高不等于出了问题，'
        '但值得去门诊复查一次，顺便和医生确认现在的用药和饮食方案要不要调整。',
    )


def build_visit_advice(user_id=None, now=None):
    """组装「就诊沟通清单」的展示数据。

    返回 dict：
        show        —— False 时模板整块不渲染（指标正常、也没连续偏高）
        level       —— 'warn' | 'critical'（show=False 时是 'ok'）
        escalated   —— True 表示"最新一次正常，但连续 N 天偏高"升上来的
        title/lead  —— 标题与首句（lead 含 <b>，模板里用 |safe）
        findings    —— 偏高指标列表（可能为空，见 escalated）
        summary     —— 一行"近 14 天记录情况"
        prep        —— 就诊准备清单 [{'title','detail'}]
        questions   —— 三个可以问医生的问题
        classes     —— 类别级科普 [{'name','how','note'}]
        classes_lead—— 类别科普前的那句话
        disclaimer  —— 免责声明（**必须同屏可见**）
        records     —— collect_records 原始返回（模板列逐条记录用）
    """
    findings, level = collect_findings(user_id)
    records = collect_records(user_id, now=now) if user_id else collect_records(None)

    base_level = level
    level = escalate_level(level, records['streak'], STREAK_DAYS)
    escalated = level != base_level

    if level == LEVEL_OK:
        return {
            'show': False, 'level': LEVEL_OK, 'escalated': False,
            'title': '', 'lead': '', 'findings': [], 'summary': records['summary_text'],
            'prep': [], 'questions': [], 'classes': [], 'classes_lead': '',
            'disclaimer': VISIT_DISCLAIMER, 'records': records,
        }

    title, lead = _title_and_lead(level, records, findings)
    return {
        'show': True,
        'level': level,
        'escalated': escalated,
        'title': title,
        'lead': lead,
        'findings': findings,
        'summary': records['summary_text'],
        'prep': build_prep(records, findings),
        'questions': build_questions(findings),
        'classes': list(DRUG_CLASSES),
        'classes_lead': DRUG_CLASSES_LEAD,
        'disclaimer': VISIT_DISCLAIMER,
        'records': records,
    }


# ---------------------------------------------------------------------------
# 自测（纯逻辑部分无需数据库；涉及库的部分建临时 SQLite）
# ---------------------------------------------------------------------------
def _self_test():
    import os
    import tempfile

    passed = total = 0

    def check(name, cond, detail=''):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print('[PASS] %s' % name)
        else:
            print('[FAIL] %s   %s' % (name, detail))

    print('=' * 78)
    print('visit_advice 自测')
    print('=' * 78)

    # ---- 1. 文本红线：不能出现药名 / 剂量 ----
    all_text = ' '.join(
        [VISIT_DISCLAIMER, DRUG_CLASSES_LEAD]
        + [d['name'] + d['how'] + d['note'] for d in DRUG_CLASSES]
        + list(_FIXED_QUESTIONS)
        + list(_QUESTION_TPL.values())
    )
    brand_names = ('二甲双胍', '格华止', '拜糖平', '阿卡波糖', '格列美脲', '格列齐特',
                   '西格列汀', '达格列净', '恩格列净', '利拉鲁肽', '司美格鲁肽',
                   '吡格列酮', '罗格列酮', '瑞格列奈', '那格列奈', '甘精胰岛素')
    hit = [n for n in brand_names if n in all_text]
    check('全文不出现具体药名', not hit, str(hit))
    dose_hit = [w for w in ('mg', '毫克', '单位/kg', '每日两次', '每日三次', '每次一片') if w in all_text]
    check('全文不出现剂量写法', not dose_hit, str(dose_hit))
    check('不出现"建议服用"', '建议服用' not in all_text and '你应该吃' not in all_text)
    check('免责声明否定处方', '不是处方' in VISIT_DISCLAIMER and '请勿自行购药' in VISIT_DISCLAIMER)
    check('免责声明给低血糖急症口径', '拨打 120' in VISIT_DISCLAIMER)
    check('类别科普交回医生判断', '由医生根据你的检查结果决定' in DRUG_CLASSES_LEAD)
    check('类别数量覆盖指南七类', len(DRUG_CLASSES) == 7, str(len(DRUG_CLASSES)))

    # ---- 2. 问题生成 ----
    f_glucose = [{'key': 'fasting_glucose', 'label': '空腹血糖', 'value_text': '7.8',
                  'unit': 'mmol/L', 'level': 'warn', 'message': ''}]
    q = build_questions(f_glucose)
    check('血糖偏高生成 3 个问题', len(q) == 3, str(q))
    check('第一个问题带上用户自己的数值', '7.8' in q[0], q[0])
    check('问题以"要不要调整"收尾而非"吃某药"', '需要调整吗' in q[0], q[0])

    q_none = build_questions([])
    check('没有偏高指标也凑够 3 个通用问题', len(q_none) == 3, str(q_none))

    f_both = [
        {'key': 'fasting_glucose', 'label': '空腹血糖', 'value_text': '8.2', 'unit': 'mmol/L',
         'level': 'warn', 'message': ''},
        {'key': 'postprandial_glucose', 'label': '餐后血糖', 'value_text': '13.1', 'unit': 'mmol/L',
         'level': 'warn', 'message': ''},
        {'key': 'systolic_bp', 'label': '收缩压', 'value_text': '168', 'unit': 'mmHg',
         'level': 'warn', 'message': ''},
    ]
    q_mix = build_questions(f_both)
    check('血糖两项只问一次（不重复）',
          sum(1 for x in q_mix if '降糖药' in x) == 1, str(q_mix))
    check('血压高时问降压药', any('降压药' in x for x in q_mix), str(q_mix))
    check('问题去重', len(set(q_mix)) == len(q_mix), str(q_mix))

    # ---- 3. prep ----
    no_rec = {'days': 14, 'count': 0, 'high_count': 0, 'rows': [], 'streak': 0,
              'summary_text': '', 'has_report': False}
    prep = build_prep(no_rec, [])
    check('没有记录时提示先补几天', any('先补几天' in p['title'] for p in prep), str(prep))
    check('准备清单至少 4 条', len(prep) >= 4, str(len(prep)))

    with_rec = {'days': 14, 'count': 9, 'high_count': 4, 'rows': [], 'streak': 3,
                'summary_text': '近 14 天记录了 9 次血糖，其中 4 次高于目标范围。',
                'has_report': True}
    prep2 = build_prep(with_rec, [])
    check('有记录时说明次数', '4 次' in prep2[0]['detail'], prep2[0]['detail'])
    check('有报告时提醒带报告', '体检报告' in prep2[1]['title'], prep2[1]['title'])

    # ---- 4. 无用户：不渲染 ----
    empty = build_visit_advice(None)
    check('没有用户时 show=False', empty['show'] is False, str(empty['show']))

    # ---- 5. 建临时库跑真实数据 ----
    tmpdir = tempfile.mkdtemp(prefix='visit_test_')
    db_path = os.path.join(tmpdir, 't.db').replace('\\', '/')
    from flask import Flask
    from models.user import User                      # noqa: F401 —— 建表需要
    from utils.database import db

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    try:
        with app.app_context():
            db.create_all()

            # 5.1 全正常 → 不渲染
            db.session.add(HealthMetric(user_id=1, report_id=None, metric_key='fasting_glucose',
                                        label='空腹血糖', value=5.4, unit='mmol/L',
                                        measured_at=datetime(2026, 10, 20, 7, 0)))
            db.session.commit()
            normal = build_visit_advice(1, now=datetime(2026, 10, 20, 9, 0))
            check('指标正常时不弹卡片', normal['show'] is False, str(normal['level']))

            # 5.2 今天 7.8 → warn
            db.session.add(HealthMetric(user_id=2, report_id=None, metric_key='fasting_glucose',
                                        label='空腹血糖', value=7.8, unit='mmol/L',
                                        measured_at=datetime(2026, 10, 20, 7, 0)))
            db.session.commit()
            warn = build_visit_advice(2, now=datetime(2026, 10, 20, 9, 0))
            check('空腹 7.8 → warn 且渲染', warn['show'] and warn['level'] == LEVEL_WARN, str(warn['level']))
            check('warn 标题说明复查', '复查' in warn['title'], warn['title'])
            check('warn 也有三个问题', len(warn['questions']) == 3, str(warn['questions']))
            check('卡片带免责声明', warn['disclaimer'] == VISIT_DISCLAIMER)
            check('findings 带用户自己的数值',
                  warn['findings'][0]['value_text'] == '7.8', str(warn['findings']))

            # 5.3 17.8 → critical
            db.session.add(HealthMetric(user_id=3, report_id=None, metric_key='fasting_glucose',
                                        label='空腹血糖', value=17.8, unit='mmol/L',
                                        measured_at=datetime(2026, 10, 20, 7, 0)))
            db.session.commit()
            crit = build_visit_advice(3, now=datetime(2026, 10, 20, 9, 0))
            check('空腹 17.8 → critical', crit['level'] == LEVEL_CRITICAL, str(crit['level']))
            check('critical 首句要求尽快就医', '尽快' in crit['lead'], crit['lead'])
            check('critical 明确不要自行加减药', '不要自行加药' in crit['lead'], crit['lead'])

            # 5.4 连续 3 天偏高，最新一次却正常 → 升级为 warn
            for i, day in enumerate((17, 18, 19)):
                db.session.add(HealthMetric(user_id=4, report_id=None, metric_key='fasting_glucose',
                                            label='空腹血糖', value=8.4, unit='mmol/L',
                                            measured_at=datetime(2026, 10, day, 7, 0)))
            db.session.add(HealthMetric(user_id=4, report_id=None, metric_key='fasting_glucose',
                                        label='空腹血糖', value=6.1, unit='mmol/L',
                                        measured_at=datetime(2026, 10, 20, 7, 0)))
            db.session.commit()
            streak = build_visit_advice(4, now=datetime(2026, 10, 20, 9, 0))
            check('连续 3 天偏高 → 升级渲染', streak['show'] and streak['level'] == LEVEL_WARN,
                  str((streak['show'], streak['level'], streak['records']['streak'])))
            check('streak 计数为 3', streak['records']['streak'] == 3,
                  str(streak['records']['streak']))
            check('升级时标记 escalated', streak['escalated'] is True, str(streak['escalated']))
            check('升级标题说明连续天数', '连续' in streak['title'], streak['title'])

            # 5.5 断档：最新一次偏高是 5 天前 → 不算"现在连续偏高"
            for day in (10, 11, 12):
                db.session.add(HealthMetric(user_id=5, report_id=None, metric_key='fasting_glucose',
                                            label='空腹血糖', value=8.4, unit='mmol/L',
                                            measured_at=datetime(2026, 10, day, 7, 0)))
            db.session.commit()
            old = build_visit_advice(5, now=datetime(2026, 10, 20, 9, 0))
            check('最新记录是 8 天前 → 不算连续', old['records']['streak'] == 0,
                  str(old['records']['streak']))
            check('但历史偏高仍会提示',
                  old['show'] and old['level'] == LEVEL_WARN, str(old['level']))

            # 5.6 用户隔离
            other = build_visit_advice(99, now=datetime(2026, 10, 20, 9, 0))
            check('看不到别人的指标', other['show'] is False, str(other['level']))

            # 5.7 血压高 + 同日记录优先于报告上传时间（与 metric_view 同口径）
            db.session.add(HealthMetric(user_id=6, report_id=700, metric_key='systolic_bp',
                                        label='收缩压', value=168, unit='mmHg',
                                        measured_at=datetime(2026, 10, 20, 8, 0)))
            db.session.commit()
            bp = build_visit_advice(6, now=datetime(2026, 10, 20, 9, 0))
            check('血压偏高也出清单', bp['level'] == LEVEL_WARN, str(bp['level']))
            check('血压问题问降压药', any('降压药' in x for x in bp['questions']), str(bp['questions']))

            # 5.8 逐条记录带来源标记
            check('记录标出记录/报告来源',
                  all(r['source'] in ('记录', '报告') for r in bp['records']['rows']),
                  str(bp['records']['rows']))
    finally:
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    import sys
    sys.exit(0 if _self_test() else 1)
