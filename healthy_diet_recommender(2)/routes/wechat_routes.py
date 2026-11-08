# routes/wechat_routes.py
"""微信小程序登录接口"""
import os
import requests
from flask import Blueprint, request, jsonify, current_app, g
from models.user import User
from utils.database import db
from utils.auth import generate_jwt_token, jwt_login_required

wechat_bp = Blueprint("wechat", __name__)

# 微信小程序配置（从环境变量读取）
WECHAT_APPID = os.environ.get("WECHAT_APPID", "wx074e14ff89c3a3be")
WECHAT_SECRET = os.environ.get("WECHAT_SECRET", "")

@wechat_bp.route("/login", methods=["POST"])
def wechat_login():
    """
    微信小程序登录接口。

    接收 wx.login() 返回的 code，调用微信 jscode2session 获取 openid。
    首次登录自动创建用户，返回 JWT token。

    Request body:
        { "code": "wx.login() 返回的临时 code" }

    Response:
        { "token": "jwt...", "user": { ... } }
    """
    data = request.get_json() or {}
    code = data.get("code")
    if not code:
        return jsonify({"message": "缺少登录凭证 code"}), 400

    # 调用微信 jscode2session 获取 openid
    try:
        wx_url = "https://api.weixin.qq.com/sns/jscode2session"
        wx_params = {
            "appid": WECHAT_APPID,
            "secret": WECHAT_SECRET,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        resp = requests.get(wx_url, params=wx_params, timeout=10)
        wx_data = resp.json()

        if "errcode" in wx_data and wx_data["errcode"] != 0:
            current_app.logger.error(f"微信登录失败: {wx_data}")
            return jsonify({"message": f"微信登录失败: {wx_data.get('errmsg', '未知错误')}"}), 400

        openid = wx_data.get("openid")
        if not openid:
            return jsonify({"message": "获取微信 openid 失败"}), 500

    except requests.RequestException as e:
        current_app.logger.error(f"调用微信 API 失败: {e}")
        return jsonify({"message": "微信服务暂时不可用，请稍后重试"}), 502

    # 查找已有用户，不存在则自动创建
    user = User.query.filter_by(username=f"wx_{openid}").first()
    is_new_user = False

    if user is None:
        is_new_user = True
        # 生成唯一的用户名和邮箱
        user = User(
            username=f"wx_{openid}",
            email=f"{openid}@wechat.local",
        )
        user.set_password(openid)  # 用 openid 作为密码（用户不会直接使用密码登录）
        db.session.add(user)
        db.session.commit()

    token = generate_jwt_token(user.id)

    return (
        jsonify(
            {
                "token": token,
                "user": user.to_dict(),
                "is_new_user": is_new_user,
            }
        ),
        200,
    )


@wechat_bp.route("/userinfo", methods=["GET"])
@jwt_login_required
def get_userinfo():
    """获取当前登录用户信息"""
    user_id = getattr(g, "user_id", None)
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "用户不存在"}), 404

    return jsonify(user.to_dict()), 200
