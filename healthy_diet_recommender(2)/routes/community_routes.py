# -*- coding: utf-8 -*-
"""R3 社区食谱分享（网页入口）。

页面路由
    GET  /api/community/                 社区广场（筛选 + 排序 + 分页）
    GET  /api/community/post/<id>        帖子详情（含同类推荐）
    GET  /api/community/mine             我的分享
动作路由
    POST /api/community/share            把一条食谱分享到社区
    POST /api/community/post/<id>/like       点赞开关（JSON）
    POST /api/community/post/<id>/favorite   收藏开关（JSON）
    POST /api/community/post/<id>/delete     删除自己的分享

约定
- 网页入口用 flash + redirect；点赞/收藏走 fetch -> JSON，页面不刷新。
- 未登录访问页面跳登录页；未登录调用 JSON 接口返回 401。
- 列表里的 `page`/`purpose` 等脏参数一律收敛，不因参数问题白屏。
"""
from flask import (Blueprint, request, jsonify, session, render_template,
                   redirect, url_for, flash)

from models.recipe import Recipe
from models.community import (
    CommunityPost, PostInteraction, LIKE, FAVORITE,
    toggle_interaction, delete_post, list_posts, list_posts_by_user,
    related_posts, find_share,
)
from utils.database import db
from utils.diabetes_advice import build_advice
from utils.recipe_text import split_steps, build_nutrition_block
from utils.recipe_options import OPTIONS

community_bp = Blueprint('community', __name__)

PER_PAGE = 10          # 老年用户：一页少放几条，翻页更轻松
SORTS = (('new', '最新'), ('hot', '最受欢迎'))


def _uid():
    return session.get('user_id')


def _page_guard():
    """页面级登录守卫：未登录 -> 跳登录页。返回 None 表示已登录。"""
    if not _uid():
        flash('请先登录后再使用社区功能', 'error')
        return redirect(url_for('user.login'))
    return None


def _json_guard():
    """接口级登录守卫：未登录 -> 401 JSON。返回 None 表示已登录。"""
    if not _uid():
        return jsonify({'message': '未登录'}), 401
    return None


def _clean_filters(args):
    """把 URL 里的筛选参数收敛成合法值（非法/空 -> None 表示不筛）。

    注意这里不用 recipe_options.normalize —— 那个会把非法值兜到**默认档**，
    用在筛选上就变成"选了个没意义的值反而筛出默认那一档"，不对。
    筛选要做的是"认不出就当没选"。
    """
    def pick(kind):
        raw = (args.get(kind) or '').strip()
        if not raw:
            return None
        keys = {k for k, *_ in OPTIONS[kind]}
        return raw if raw in keys else None

    return {k: pick(k) for k in ('purpose', 'flavor', 'meal_time')}


@community_bp.route('/', methods=['GET'])
def square():
    guard = _page_guard()
    if guard:
        return guard

    f = _clean_filters(request.args)
    sort = request.args.get('sort') if request.args.get('sort') in dict(SORTS) else 'new'
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1

    posts, total, page, pages = list_posts(
        purpose=f['purpose'], flavor=f['flavor'], meal_time=f['meal_time'],
        sort=sort, page=page, per_page=PER_PAGE)

    viewer = _uid()
    return render_template(
        'community/square.html',
        posts=posts, total=total, page=page, pages=pages,
        sort=sort, sorts=SORTS, filters=f,
        options=OPTIONS,        # 三个维度的选项表，模板里直接 for 出来
        viewer_id=viewer,
        liked_ids=_interaction_ids(viewer, LIKE),
        faved_ids=_interaction_ids(viewer, FAVORITE),
    )


def _interaction_ids(user_id, kind):
    """当前用户在这批帖子里的互动集合，避免模板里一条一条查。"""
    if not user_id:
        return set()
    rows = db.session.query(PostInteraction.post_id) \
        .filter(PostInteraction.user_id == user_id,
                PostInteraction.type == kind).all()
    return {r[0] for r in rows}


@community_bp.route('/post/<int:post_id>', methods=['GET'])
def post_detail(post_id):
    guard = _page_guard()
    if guard:
        return guard

    post = db.session.get(CommunityPost, post_id)
    if not post:
        flash('这条分享不存在或已被删除', 'error')
        return redirect(url_for('community.square'))

    viewer = _uid()
    recipe = post.recipe
    return render_template(
        'community/detail.html',
        post=post,
        related=related_posts(post),
        viewer_id=viewer,
        is_author=(post.user_id == viewer),
        liked=post.liked_by(viewer),
        faved=post.favorited_by(viewer),
        liked_ids=_interaction_ids(viewer, LIKE),
        faved_ids=_interaction_ids(viewer, FAVORITE),
        # 别人的食谱也照样给控糖提示 —— 糖友看的每道菜都该有这层信息
        advice=build_advice(recipe.ingredients, recipe.servings) if recipe else None,
        # 步骤 / 营养和自家详情页用同一套清洗与拆解，避免两处显示不一致
        steps=split_steps(recipe.instructions) if recipe else [],
        nut=build_nutrition_block(recipe.nutrition_info) if recipe else None,
    )


@community_bp.route('/mine', methods=['GET'])
def mine():
    guard = _page_guard()
    if guard:
        return guard

    viewer = _uid()
    posts = list_posts_by_user(viewer)
    return render_template(
        'community/mine.html',
        posts=posts,
        viewer_id=viewer,
        liked_ids=_interaction_ids(viewer, LIKE),
        faved_ids=_interaction_ids(viewer, FAVORITE),
    )


@community_bp.route('/share', methods=['POST'])
def share():
    """把食谱分享到社区。表单和 JSON 两种提交都支持。"""
    uid = _uid()
    if not uid:
        if request.is_json:
            return jsonify({'message': '未登录'}), 401
        flash('请先登录后再分享', 'error')
        return redirect(url_for('user.login'))

    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}
    try:
        recipe_id = int(data.get('recipe_id'))
    except (TypeError, ValueError):
        if request.is_json:
            return jsonify({'message': '缺少食谱编号'}), 400
        flash('分享失败：缺少食谱编号', 'error')
        return redirect(url_for('recipe.get_recipes'))

    recipe = db.session.get(Recipe, recipe_id)
    if not recipe or recipe.user_id != uid:
        if request.is_json:
            return jsonify({'message': '食谱不存在'}), 404
        flash('分享失败：找不到这条食谱', 'error')
        return redirect(url_for('recipe.get_recipes'))

    existed = find_share(uid, recipe_id)
    if existed:
        if request.is_json:
            return jsonify({'message': '这条食谱你已经分享过了',
                            'post_id': existed.id}), 200
        flash('这条食谱你已经分享过了', 'info')
        return redirect(url_for('community.post_detail', post_id=existed.id))

    note = (data.get('note') or '').strip()[:200]     # 分享语限长，防刷屏
    post = CommunityPost(recipe_id=recipe_id, user_id=uid, note=note or None)
    db.session.add(post)
    try:
        db.session.commit()
    except Exception:
        # 唯一索引兜底（连点两次、两个标签页同时提交）
        db.session.rollback()
        existed = find_share(uid, recipe_id)
        if request.is_json:
            return jsonify({'message': '这条食谱你已经分享过了',
                            'post_id': existed.id if existed else None}), 200
        flash('这条食谱你已经分享过了', 'info')
        if existed:
            return redirect(url_for('community.post_detail', post_id=existed.id))
        return redirect(url_for('recipe.get_recipes'))

    if request.is_json:
        return jsonify({'message': '已分享到社区', 'post_id': post.id,
                        'post': post.to_dict(uid)}), 201
    flash('已分享到社区，其他用户现在可以看到啦', 'success')
    return redirect(url_for('community.post_detail', post_id=post.id))


@community_bp.route('/post/<int:post_id>/like', methods=['POST'])
def like(post_id):
    return _toggle(post_id, LIKE)


@community_bp.route('/post/<int:post_id>/favorite', methods=['POST'])
def favorite(post_id):
    return _toggle(post_id, FAVORITE)


def _toggle(post_id, kind):
    guard = _json_guard()
    if guard:
        return guard

    uid = _uid()
    post = db.session.get(CommunityPost, post_id)
    if not post:
        return jsonify({'message': '这条分享不存在或已被删除'}), 404

    active = toggle_interaction(post, uid, kind)
    db.session.commit()
    return jsonify({
        'message': ('已取消' if not active else ('点赞成功' if kind == LIKE else '收藏成功')),
        'active': active,
        'like_count': post.like_count,
    })


@community_bp.route('/post/<int:post_id>/delete', methods=['POST'])
def remove(post_id):
    uid = _uid()
    if not uid:
        if request.is_json:
            return jsonify({'message': '未登录'}), 401
        flash('请先登录', 'error')
        return redirect(url_for('user.login'))

    post = db.session.get(CommunityPost, post_id)
    if not post:
        flash('这条分享已经不在社区里了', 'info')
        return redirect(url_for('community.mine'))
    if post.user_id != uid:
        # 只能删自己的 —— 直接拒绝，别给"删别人帖子"留口子
        if request.is_json:
            return jsonify({'message': '只能删除自己分享的食谱'}), 403
        flash('只能删除自己分享的食谱', 'error')
        return redirect(url_for('community.post_detail', post_id=post_id))

    delete_post(post)
    db.session.commit()

    if request.is_json:
        return jsonify({'message': '已从社区删除'}), 200
    flash('已从社区删除', 'success')
    return redirect(url_for('community.mine'))
