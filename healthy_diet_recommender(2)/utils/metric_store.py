# utils/metric_store.py
"""
健康指标落库 / 查询 —— `utils/report_parser.py` 的下游

分工
    report_parser  = 从报告文本里抠出数字（纯规则，不碰数据库）
    metric_store   = 把抠出来的数字存进 `health_metric` 表，并提供"最新值 / 趋势"查询
    risk_guard     = 按阈值判断要不要拦住生成

为什么单独一层
    路由里直接写数据库读写会让上传接口变脆，也让"解析规则"和"存储结构"耦合。
    这里把落库收敛成一个函数，出问题时有明确的安全兜底（见下）。

安全兜底（重要）
    `save_metrics_*` 内部自己 try/except + rollback，**失败只返回错误信息、绝不抛异常**。
    调用方（上传接口）因此不会因为"落库失败"而丢掉用户刚上传的报告。
    抽不到任何指标时直接返回 saved=0，不写任何行。

自测
    python utils/metric_store.py
"""
from datetime import datetime, date as _date

from utils.biz_time import utc_now
from utils.database import db
from utils.report_parser import parse_health_report, DISPLAY_ORDER
from models.health_metric import HealthMetric

# 未在展示顺序里登记的键，排到最后
_ORDER_INDEX = {key: idx for idx, key in enumerate(DISPLAY_ORDER)}

# latest_metrics 排序兜底：measured_at 理论上不会为空（列有 default），
# 但历史脏数据里出现过 NULL，排序时不能让它抛 TypeError。
_MIN_DATE = _date.min
_MIN_DATETIME = datetime.min


def _order_of(key):
    return _ORDER_INDEX.get(key, len(_ORDER_INDEX))


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def save_metrics_for_text(user_id, text, report_id=None, measured_at=None):
    """
    解析报告文本并把指标写入 `health_metric`。

    Args:
        user_id: 指标归属用户
        text: 报告原文（OCR 结果）
        report_id: 来源报告 id；给了就会先清掉该报告的旧行再写，保证幂等
        measured_at: 测量时间，默认当前时间

    Returns:
        dict: {"saved": 写入行数, "found": 抽到指标数, "error": 出错信息或 None}
              —— 永不抛异常
    """
    parsed = parse_health_report(text)
    metrics = parsed["metrics"]
    if not metrics:
        return {"saved": 0, "found": 0, "error": None}

    # 存储统一 UTC（与 models 的 utcnow 默认值同口径）。用 biz_time.utc_now()
    # 而不是 datetime.utcnow()：后者在 3.12+ 已弃用，且机器时区一变就没人说得清。
    when = measured_at or utc_now()

    try:
        if report_id is not None:
            # 同一份报告重复解析时覆盖，不产生重复行
            HealthMetric.query.filter_by(report_id=report_id).delete(
                synchronize_session=False
            )

        rows = [
            HealthMetric(
                user_id=user_id,
                report_id=report_id,
                metric_key=key,
                label=parsed["labels"].get(key, key),
                value=float(value),
                unit=parsed["units"].get(key, ""),
                measured_at=when,
            )
            for key, value in metrics.items()
        ]
        db.session.add_all(rows)
        db.session.commit()
        return {"saved": len(rows), "found": len(metrics), "error": None}
    except Exception as exc:                      # noqa: BLE001 —— 兜底，不外抛
        db.session.rollback()
        return {"saved": 0, "found": len(metrics), "error": str(exc)}


def save_metrics_for_report(report):
    """便捷入口：直接从 `HealthReport` 对象落库（用报告上传时间作为测量时间）。"""
    if report is None:
        return {"saved": 0, "found": 0, "error": None}
    return save_metrics_for_text(
        user_id=report.user_id,
        text=report.content_text,
        report_id=report.id,
        measured_at=report.uploaded_at,
    )


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def latest_metrics(user_id):
    """
    每位用户每个指标取**最新一次**的值，附带历史点位数。

    Returns:
        list[dict]: 按展示顺序（身高体重 → 血压 → 血糖 → 血脂 → 尿酸）排列
                    [{"key","label","unit","value","measured_at","report_id","points"}, ...]
        没有任何数据时返回 []
    """
    rows = HealthMetric.query.filter_by(user_id=user_id) \
        .order_by(HealthMetric.measured_at.asc(), HealthMetric.id.asc()).all()

    # 覆盖顺序 = 「日期 → 打卡优先 → 时刻 → id」，后写覆盖前写。
    #
    # 为什么不能只按 measured_at 排（R5 打卡上线时踩过的坑）：
    #   报告行的 measured_at 其实是**上传时间**（save_metrics_for_report 传的是
    #   report.uploaded_at），不是报告上的检测日期。用户今天把上周的报告传上来，
    #   它就带着"今天"的时间戳，会把今天早上刚填的血糖顶掉 —— 用户看到的现象就是
    #   "我明明填了 7.8，怎么变成了报告里的 6.5"。
    #   所以同一天里让打卡行（report_id 为 None）排在后面，即优先展示用户亲手测的值。
    #   报告行仍原封不动留在库里，报告详情页照常显示 6.5，两条都不丢。
    rows.sort(key=lambda r: (
        r.measured_at.date() if r.measured_at else _MIN_DATE,
        1 if r.report_id is None else 0,      # 同一天：打卡（1）压过报告（0）
        r.measured_at or _MIN_DATETIME,
        r.id or 0,
    ))

    latest = {}
    counts = {}
    for row in rows:
        latest[row.metric_key] = row          # 排序靠后的覆盖成最新
        counts[row.metric_key] = counts.get(row.metric_key, 0) + 1

    result = []
    for key, row in latest.items():
        item = row.to_dict()
        item['points'] = counts.get(key, 1)
        result.append(item)
    result.sort(key=lambda item: _order_of(item['key']))
    return result


def metric_trend(user_id, key, limit=30):
    """
    单个指标的时间序列，供画趋势图。

    Returns:
        dict: {"key","label","unit","points":[{"value","measured_at","report_id"}],"count"}
              该指标没有数据时 points 为 []
    """
    rows = HealthMetric.query.filter_by(user_id=user_id, metric_key=key) \
        .order_by(HealthMetric.measured_at.desc(), HealthMetric.id.desc()) \
        .limit(max(1, int(limit))).all()
    rows.reverse()                            # 返回时按时间正序，前端直接画

    label = rows[0].label if rows else key
    unit = rows[0].unit if rows else ''
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "count": len(rows),
        "points": [
            {
                "value": row.value,
                "measured_at": row.measured_at.isoformat() if row.measured_at else None,
                "report_id": row.report_id,
            }
            for row in rows
        ],
    }


def user_report_ids_with_metrics(user_id):
    """已经落过指标的报告 id 集合，回填脚本用来跳过或判断。"""
    rows = db.session.query(HealthMetric.report_id) \
        .filter(HealthMetric.user_id == user_id,
                HealthMetric.report_id.isnot(None)).distinct().all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# 自测：用临时 SQLite 文件跑，不接触真实 app.db，也不需要启动 Flask
# ---------------------------------------------------------------------------
_SAMPLE = "身高 170cm 体重 78.5kg 血压 145/92 空腹血糖 8.9 mmol/L 糖化血红蛋白 7.8%"
_SAMPLE2 = "空腹血糖 7.1 mmol/L 甘油三酯 2.2 mmol/L"


def _build_test_app(db_path):
    """搭一个最小 Flask 应用（只挂数据库），避免 import 主 app 带来的重依赖。"""
    from flask import Flask
    from models.user import User          # noqa: F401 —— 建表需要，外键指向它
    from models.health_report import HealthReport  # noqa: F401

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
    db.init_app(app)
    return app


def _self_test():
    import os
    import sys
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix='health_metric_test_')
    db_path = os.path.join(tmpdir, 't.db').replace('\\', '/')
    app = _build_test_app(db_path)

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
    print('metric_store 自测')
    print('=' * 78)

    try:
        with app.app_context():
            db.create_all()

            # 1) 正常落库：身高/体重/BMI 三项 + 血压两项 + 血糖 + 糖化 = 7 项
            r1 = save_metrics_for_text(1, _SAMPLE, report_id=101,
                                       measured_at=datetime(2026, 9, 1, 8, 0))
            check('落库写入 7 个指标', r1['saved'] == 7, str(r1))

            # 2) 幂等：同一报告重复落库不产生重复行
            save_metrics_for_text(1, _SAMPLE, report_id=101,
                                  measured_at=datetime(2026, 9, 1, 8, 0))
            check('同一报告重复落库不重复', HealthMetric.query.filter_by(report_id=101).count() == 7,
                  str(HealthMetric.query.filter_by(report_id=101).count()))

            # 3) 空文本 / None 不写行、不报错
            r2 = save_metrics_for_text(1, '', report_id=102)
            r3 = save_metrics_for_text(1, None, report_id=103)
            check('空文本不写行且无错', r2['saved'] == 0 and r2['error'] is None, str(r2))
            check('None 不写行且无错', r3['saved'] == 0 and r3['error'] is None, str(r3))

            # 4) 第二次报告（更晚时间）
            save_metrics_for_text(1, _SAMPLE2, report_id=104,
                                  measured_at=datetime(2026, 9, 10, 8, 0))

            # 5) 最新值：血糖应取第二次的 7.1，身高仍是第一次的 170
            latest = {item['key']: item for item in latest_metrics(1)}
            check('最新值取最近一次(血糖 7.1)',
                  latest.get('fasting_glucose', {}).get('value') == 7.1, str(latest.get('fasting_glucose')))
            check('旧指标仍保留(身高 170)',
                  latest.get('height', {}).get('value') == 170, str(latest.get('height')))
            check('历史点位数统计正确',
                  latest.get('fasting_glucose', {}).get('points') == 2,
                  str(latest.get('fasting_glucose')))

            # 6) 展示顺序：身高在所有指标之前，血糖在血脂之前
            keys = [item['key'] for item in latest_metrics(1)]
            check('展示顺序按 身高→血压→血糖→血脂 排列',
                  keys.index('height') < keys.index('systolic_bp') < keys.index('fasting_glucose')
                  < keys.index('triglyceride'), str(keys))

            # 7) 趋势：时间正序、单位与标签带上
            trend = metric_trend(1, 'fasting_glucose')
            check('趋势按时间正序返回',
                  [p['value'] for p in trend['points']] == [8.9, 7.1], str(trend))
            check('趋势带中文名与单位',
                  trend['label'] == '空腹血糖' and trend['unit'] == 'mmol/L', str(trend))

            # 8) 没有数据的指标：返回空序列而不是报错
            empty = metric_trend(1, 'uric_acid')
            check('无数据指标返回空序列', empty['points'] == [] and empty['key'] == 'uric_acid', str(empty))

            # 9) 用户隔离：用户 2 看不到用户 1 的数据
            check('用户之间数据隔离', latest_metrics(2) == [], str(latest_metrics(2)))

            # 10) limit 生效
            limited = metric_trend(1, 'fasting_glucose', limit=1)
            check('limit 生效', len(limited['points']) == 1 and limited['points'][0]['value'] == 7.1,
                  str(limited))

            # 11) 落库失败不抛异常：注入一个写库必炸的假 session，验证 try/except + rollback
            #     （SQLite 不强制外键、列类型也宽松，靠脏数据触发不了异常，所以用注入）
            #     注意：本文件直接运行时 __name__ == '__main__'，必须改「正在运行的那个模块」
            #     的全局变量，不能 import 成 utils.metric_store（那是另一个模块实例）。
            import sys

            module = sys.modules[__name__]

            class _BoomSession:
                rolls = 0

                def add_all(self, rows):
                    raise RuntimeError('模拟数据库故障')

                def commit(self):
                    pass

                def rollback(self):
                    _BoomSession.rolls += 1

            class _BoomDB:
                def __init__(self):
                    self.session = _BoomSession()

            real_db = module.db
            module.db = _BoomDB()
            try:
                bad = save_metrics_for_text(1, _SAMPLE)   # report_id=None，直达 add_all
            finally:
                module.db = real_db

            check('写库异常被吞掉且返回 error',
                  bad['saved'] == 0 and bad['error'] is not None, str(bad))
            check('写库异常后执行了 rollback', _BoomSession.rolls == 1, str(_BoomSession.rolls))
            check('异常后会话仍可查询', isinstance(latest_metrics(1), list))
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
