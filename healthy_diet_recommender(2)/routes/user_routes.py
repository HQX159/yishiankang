# routes/user_routes.py
from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template
from models.user import User
from utils.database import db
from utils.auth import login_guard
from werkzeug.security import generate_password_hash

user_bp = Blueprint('user', __name__)


def check_user_logged_in():
    """未登录守卫：页面跳登录页、接口回 401 JSON（判据统一在 utils.auth）。

    `/profile`、`/settings`、`/change_password` 都是**页面**，而且后两个还是表单提交 ——
    session 过期后回 401 JSON 的话，用户会看到裸 JSON 且丢掉刚填的内容。
    """
    return login_guard()


@user_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Handle form data instead of JSON
        identifier = request.form.get('identifier') # Use request.form
        password = request.form.get('password')

        if not identifier or not password:
            # Flash message for template rendering
            from flask import flash
            flash('请输入用户名和密码', 'error')
            return render_template('auth/login.html')

        # Query by username, email, or phone (assuming a phone field exists)
        user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()
        if not user or not user.check_password(password):
            from flask import flash
            flash('用户名或密码错误', 'error')
            return render_template('auth/login.html')

        session['user_id'] = user.id
        # Flash success message and redirect
        from flask import flash
        flash('登录成功', 'success')
        return redirect(url_for('dashboard'))

    # GET 请求返回登录页面
    return render_template('auth/login.html')


@user_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        from flask import flash
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''

        if not username or not password:
            flash('请填写用户名和密码', 'error')
            return render_template('auth/register.html')

        if not (2 <= len(username) <= 20):
            flash('用户名需 2～20 个字', 'error')
            return render_template('auth/register.html')

        if len(password) < 6:
            flash('密码至少 6 位', 'error')
            return render_template('auth/register.html')

        if confirm and confirm != password:
            flash('两次输入的密码不一致', 'error')
            return render_template('auth/register.html')

        if User.query.filter_by(username=username).first():
            flash('用户名已存在，请换一个', 'error')
            return render_template('auth/register.html')

        # 邮箱选填：填了就查重；没填则写占位值
        # （老库 email 是 NOT NULL + UNIQUE，占位保证不破坏约束；与微信登录同一做法）
        if email:
            if User.query.filter_by(email=email).first():
                flash('邮箱已被注册', 'error')
                return render_template('auth/register.html')
        else:
            email = 'u%s@local' % username

        user = User(username=username, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash('注册成功，请登录', 'success')
        return redirect(url_for('user.login'))

    # GET 请求返回注册页面
    return render_template('auth/register.html')


@user_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    from flask import flash
    flash('您已成功退出登录', 'info')
    return redirect(url_for('user.login')) # Redirect to login page after logout


@user_bp.route('/profile')
def profile():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    user = User.query.get(user_id)
    if not user:
        return jsonify({'message': '用户不存在'}), 404

    return jsonify(user.to_dict()), 200

@user_bp.route('/settings')
def settings():
    """设置页（R1 适老化：阅读字号等）。登录后才能进。"""
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    user = User.query.get(user_id)
    if not user:
        from flask import flash
        flash('登录已过期，请重新登录', 'error')
        return redirect(url_for('user.login'))

    return render_template('auth/settings.html')


@user_bp.route('/change_password', methods=['GET', 'POST'])
def change_password():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        from flask import flash
        if not old_password or not new_password or not confirm_password:
            flash('请填写完整信息', 'error')
            return render_template('auth/change_password.html')

        if len(new_password) < 6:
            flash('新密码至少6位', 'error')
            return render_template('auth/change_password.html')

        if new_password != confirm_password:
            flash('两次输入的新密码不一致', 'error')
            return render_template('auth/change_password.html')

        user = User.query.get(user_id)
        if not user:
            flash('用户不存在', 'error')
            return redirect(url_for('user.login'))

        if not user.check_password(old_password):
            flash('原密码错误', 'error')
            return render_template('auth/change_password.html')

        user.set_password(new_password)
        db.session.commit()

        from flask import flash
        flash('密码修改成功，请重新登录', 'success')
        session.pop('user_id', None)
        return redirect(url_for('user.login'))

    return render_template('auth/change_password.html')