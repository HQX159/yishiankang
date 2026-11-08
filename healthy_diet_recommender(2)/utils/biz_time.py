# -*- coding: utf-8 -*-
# utils/biz_time.py
"""业务时区（北京时间 UTC+8）与存储时区（UTC）的换算。

为什么要有这个模块
    线上容器 `TZ` 是空的，`datetime.now()` 返回 **UTC**；而开发机是 UTC+8，
    `datetime.now()` 返回**北京时间**。同一行代码在两台机器上语义不同，
    于是有了这个真实故障（2026-10-24 线上发现）：

        北京时间早上 7:00 记一次空腹血糖
        -> 服务器存成 UTC 前一天 23:00
        -> "今天"却按 UTC 算 -> 卡片显示"今天还没记录"、输入框变空
        -> 用户 8:00 再打开，以为刚记的数据丢了

    更麻烦的是**本地测试永远测不出来**：开发机本身就是 UTC+8，两边一致。

三条约定（改时间相关的代码前先读这三条）
    1. **存储统一 UTC**（naive，不带 tzinfo）。
       与历史数据、与 `models/*.py` 里的 `default=datetime.utcnow` 口径一致，
       所以**不需要任何数据迁移**。
    2. **"哪一天"一律按北京时间判断**，日期给人看时也换成北京时间。
       血糖是"每天早上量的"这件事本身就是北京时间的语义。
    3. **任何地方都不许再裸写 `datetime.now()`**（它取决于机器时区）。
       取当前时刻 -> `utc_now()`；取"北京时间的现在" -> `biz_now()`；
       要某一天在 UTC 下的区间 -> `biz_day_bounds()`。

自测
    "C:/Users/123/.workbuddy/binaries/python/versions/3.13.12/python.exe" utils/biz_time.py
"""
from datetime import datetime, timedelta, timezone

# 业务时区固定东八区。**不做可配置**：这是个面向中国糖尿病患者的产品，
# 引入配置项只会让"忘了配"变成新的故障源。
BIZ_OFFSET = timedelta(hours=8)


# ---------------------------------------------------------------------------
# 取"现在"
# ---------------------------------------------------------------------------
def utc_now():
    """当前时刻的 naive UTC。

    不用 `datetime.now()`（机器时区），也不用 `datetime.utcnow()`
    （Python 3.12+ 已弃用、会打 DeprecationWarning）。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def biz_now():
    """当前时刻的北京时间（naive）。"""
    return utc_now() + BIZ_OFFSET


# ---------------------------------------------------------------------------
# 换算
# ---------------------------------------------------------------------------
def to_biz(dt):
    """库里存的 naive UTC -> 北京时间（naive）。None 原样返回。"""
    return None if dt is None else dt + BIZ_OFFSET


def to_utc(dt):
    """北京 naive 时间 -> naive UTC。None 原样返回。"""
    return None if dt is None else dt - BIZ_OFFSET


# ---------------------------------------------------------------------------
# 一天的边界
# ---------------------------------------------------------------------------
def biz_day_bounds(base_utc=None):
    """返回"北京时间某一天"在 **UTC** 下的区间 [start, end)。

    默认是"北京时间的今天"。返回值直接拿去和 `measured_at`（UTC）比较，
    不用调用方再自己减 8 小时 —— 这个减法错一次就是整整一天的偏移。

    >>> # 北京时间 10-25 07:00 == UTC 10-24 23:00
    >>> s, e = biz_day_bounds(datetime(2026, 10, 24, 23, 0))
    >>> (s, e)
    (datetime.datetime(2026, 10, 24, 16, 0), datetime.datetime(2026, 10, 25, 16, 0))
    """
    base = base_utc if base_utc is not None else utc_now()
    biz = base + BIZ_OFFSET
    start_biz = biz.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_biz - BIZ_OFFSET, start_biz + timedelta(days=1) - BIZ_OFFSET


def biz_date(dt):
    """库里 UTC 时间 -> 北京日期（date 对象）。None -> None。"""
    if dt is None:
        return None
    return (dt + BIZ_OFFSET).date()


def biz_date_str(dt=None, fmt='%Y-%m-%d', empty=''):
    """库里 UTC 时间 -> 北京日期字符串；dt 为 None 时取"北京今天"。"""
    value = dt if dt is not None else utc_now()
    if not isinstance(value, datetime):
        return empty
    return (value + BIZ_OFFSET).strftime(fmt)


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
def _self_test():
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
    print('biz_time 自测')
    print('=' * 78)

    # 1) utc_now 是真正的 UTC，不受机器时区影响
    real_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    gap = abs((real_utc - utc_now()).total_seconds())
    check('utc_now 是真正的 UTC（与本机时区无关）', gap < 2, '差 %.1f 秒' % gap)
    # 开发机是 UTC+8，所以 datetime.now() 一定比 utc_now() 晚约 8 小时 ——
    # 这一条是在**证明**"本地和线上 datetime.now() 语义不同"（故障根因）。
    local_gap = (datetime.now() - utc_now()).total_seconds()
    print('   本机 datetime.now() 比 utc_now() 晚 %.1f 小时（线上容器是 0）' % (local_gap / 3600))

    # 2) 北京 07:00 == UTC 前一天 23:00，仍然属于"北京今天"
    bj_0700 = datetime(2026, 10, 25, 7, 0)          # 用户以为的"今天早上 7 点"
    utc_stamp = to_utc(bj_0700)                      # 服务器实际会存成这个
    check('北京 07:00 存成 UTC 前一天 23:00',
          utc_stamp == datetime(2026, 10, 24, 23, 0), str(utc_stamp))

    start, end = biz_day_bounds(utc_stamp)
    check('这条记录落在"北京 10-25"的窗口里',
          start <= utc_stamp < end, '%s ~ %s' % (start, end))
    check('窗口两端是 UTC 16:00 到次日 16:00',
          start == datetime(2026, 10, 24, 16, 0) and end == datetime(2026, 10, 25, 16, 0),
          '%s ~ %s' % (start, end))

    # 3) 边界附近：北京 23:59:59 与次日 00:00:01 必须分属两天
    s1, e1 = biz_day_bounds(to_utc(datetime(2026, 10, 25, 23, 59, 59)))
    s2, _ = biz_day_bounds(to_utc(datetime(2026, 10, 26, 0, 0, 1)))
    check('北京 23:59:59 与次日 00:00:01 不在同一天窗口', e1 == s2, '%s | %s' % (e1, s2))

    # 4) 日期显示跟着北京时间走
    check('UTC 10-24 23:00 显示成 10-25',
          biz_date_str(datetime(2026, 10, 24, 23, 0)) == '2026-10-25',
          biz_date_str(datetime(2026, 10, 24, 23, 0)))
    check('UTC 10-24 15:00 仍显示 10-24',
          biz_date_str(datetime(2026, 10, 24, 15, 0)) == '2026-10-24',
          biz_date_str(datetime(2026, 10, 24, 15, 0)))
    check('None 取"北京今天"，不是空串', biz_date_str(None) == biz_now().strftime('%Y-%m-%d'),
          biz_date_str(None))
    check('biz_date(None) 返回 None', biz_date(None) is None, str(biz_date(None)))

    # 5) 往返不丢精度
    x = datetime(2026, 3, 8, 12, 34, 56, 789123)
    check('to_utc / to_biz 往返一致', to_biz(to_utc(x)) == x, str(to_biz(to_utc(x))))

    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    import sys
    sys.exit(0 if _self_test() else 1)
