# -*- coding: utf-8 -*-
# routes/checkin_routes.py
"""每日血糖记录的写入入口

为什么用普通表单 POST，而不是 fetch + 局部刷新
    目标用户是中老年糖友。9 个按钮里只要有一个 JS 报错，fetch 版本就是"点了没反应"，
    而普通表单提交在**任何**浏览器、甚至 JS 全挂的情况下都能用。
    PRG（POST → Redirect → GET）还能天然避免用户按 F5 重复提交。
    代价只是刷新一次页面，换来的是"一定能用"。

网页端 / 鸿蒙与小程序
    这个蓝图只做**网页**的写入（普通表单 POST + 重定向）。
    鸿蒙 / 小程序走 JSON 接口 `GET/POST /api/v1/health/checkin`（`routes/api_v1.py`），
    两条路上层不同、**校验与落库是同一份代码**（`utils/checkin.py`），
    所以不会出现"网页能存、手机报不是数字"。
"""
from flask import Blueprint, flash, redirect, request, session, url_for

from utils.auth import login_required
from utils.checkin import parse_checkin, save_checkin

checkin_bp = Blueprint('checkin', __name__)


def _num_text(value):
    """7.80 → 7.8，72.0 → 72。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ('%.2f' % number).rstrip('0').rstrip('.')


def _safe_next(raw, fallback):
    """只允许跳回站内相对路径，挡掉 //evil.com 这类开放重定向。"""
    text = str(raw or '').strip()
    if text.startswith('/') and not text.startswith('//') and '\\' not in text:
        return text
    return fallback


@checkin_bp.route('/save', methods=['POST'])
@login_required
def save():
    user_id = session['user_id']
    fallback = url_for('dashboard')
    target = _safe_next(request.form.get('next'), fallback)

    items, errors = parse_checkin(request.form)

    if errors:
        for _, message in errors:
            flash(message, 'error')
        return redirect(target)

    if not items:
        flash('还没有填数值哦，填一个就能存', 'error')
        return redirect(target)

    result = save_checkin(user_id, items)
    if result['error']:
        # 具体异常不外露给用户（可能是数据库细节），只提示重试
        flash('保存失败了，请再试一次', 'error')
        return redirect(target)

    done = '、'.join('%s %s %s' % (i['label'], _num_text(i['value']), i['unit'])
                     for i in items)
    flash('已记录：' + done, 'success')
    return redirect(target)
