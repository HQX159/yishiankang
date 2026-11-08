# -*- coding: utf-8 -*-
"""R3 社区食谱分享的两张表。

- CommunityPost    一条「分享到社区」的帖子，指向某条 Recipe
- PostInteraction  点赞 / 收藏，按 (user_id, post_id, type) 去重

设计取舍
--------
1. **帖子只存引用，不复制正文**：正文仍在 `recipe` 表。这样食谱以后被修正，
   社区里看到的也跟着修正，不会出现两处不一致。
2. **like_count 是缓存列**，真值在 post_interaction。每次点赞/取消都用
   `recount_likes()` 重算一遍（条数很少，一次 count 查询，不值得为省这点开销
   引入"增量 + 可能漂移"的风险）。自测里有一条专门校验两者一致。
3. **同一个人同一条食谱只能分享一次**（唯一索引 ux_community_post_user_recipe）：
   老年用户容易连点，也能防重复刷屏。
4. 删除帖子时**连带删掉它的互动**（SQLite 默认不开外键级联，代码里显式删）。
"""
from datetime import datetime

from utils.database import db
from models.recipe import Recipe          # 三维度标签在 recipe 表上，筛选要 join 过去

LIKE = 'like'
FAVORITE = 'favorite'
INTERACTION_TYPES = (LIKE, FAVORITE)


class CommunityPost(db.Model):
    __tablename__ = 'community_post'

    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    note = db.Column(db.Text)                                    # 分享语，可空
    like_count = db.Column(db.Integer, nullable=False, default=0)  # 缓存，见模块文档
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recipe = db.relationship('Recipe', backref=db.backref('community_posts', lazy=True))
    author = db.relationship('User', backref=db.backref('community_posts', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'recipe_id', name='ux_community_post_user_recipe'),
    )

    # ── 给模板 / 接口用的属性 ──────────────────────────────────
    @property
    def author_name(self):
        """作者显示名。模型里 User 没有昵称字段，直接用用户名。"""
        return self.author.username if self.author else '已注销用户'

    @property
    def title(self):
        return self.recipe.title if self.recipe else '（食谱已删除）'

    def tags(self):
        """三维度标签，只返回有值的 —— 页面据此渲染，没标签就整行不显示。"""
        if not self.recipe:
            return []
        r = self.recipe
        return [(label, value) for label, value in (
            ('目的', r.purpose_label), ('口味', r.flavor_label), ('时段', r.meal_time_label)
        ) if value]

    def liked_by(self, user_id):
        return self._has(user_id, LIKE)

    def favorited_by(self, user_id):
        return self._has(user_id, FAVORITE)

    def _has(self, user_id, kind):
        if not user_id:
            return False
        return PostInteraction.query.filter_by(
            post_id=self.id, user_id=user_id, type=kind).first() is not None

    def recount_likes(self):
        """按 post_interaction 重算点赞数并写回缓存列（不提交，由调用方 commit）。"""
        n = db.session.query(db.func.count()).select_from(PostInteraction) \
            .filter(PostInteraction.post_id == self.id,
                    PostInteraction.type == LIKE).scalar() or 0
        self.like_count = n
        return n

    def to_dict(self, viewer_id=None):
        return {
            'id': self.id,
            'recipe_id': self.recipe_id,
            'user_id': self.user_id,
            'author_name': self.author_name,
            'note': self.note or '',
            'like_count': self.like_count,
            'liked': self.liked_by(viewer_id),
            'favorited': self.favorited_by(viewer_id),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'recipe': self.recipe.to_dict() if self.recipe else None,
            'tags': [{'label': a, 'value': b} for a, b in self.tags()],
        }


class PostInteraction(db.Model):
    __tablename__ = 'post_interaction'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('community_post.id'), nullable=False)
    type = db.Column(db.String(16), nullable=False)      # 'like' / 'favorite'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'post_id', 'type', name='ux_interaction_user_post_type'),
    )


def toggle_interaction(post, user_id, kind):
    """点赞/收藏开关。返回 (现在是否处于该状态, 需要 commit)。

    kind 只能是 'like' / 'favorite'；非法值抛 ValueError（路由层兜住给 400）。
    """
    if kind not in INTERACTION_TYPES:
        raise ValueError('未知互动类型: %r' % (kind,))

    row = PostInteraction.query.filter_by(
        post_id=post.id, user_id=user_id, type=kind).first()
    if row:
        db.session.delete(row)
        active = False
    else:
        db.session.add(PostInteraction(post_id=post.id, user_id=user_id, type=kind))
        active = True

    if kind == LIKE:
        db.session.flush()          # 让计数把刚加的/删的算进去
        post.recount_likes()
    return active


def delete_post(post):
    """删帖：先清互动再删帖子。SQLite 默认不级联，这里显式做。"""
    PostInteraction.query.filter_by(post_id=post.id).delete(synchronize_session=False)
    db.session.delete(post)


# ── 查询助手 ──────────────────────────────────────────────────
# 三维度标签存在 recipe 表上，所以筛选要 join 过去。

def _filtered(purpose=None, flavor=None, meal_time=None):
    q = CommunityPost.query.join(Recipe, CommunityPost.recipe_id == Recipe.id)
    if purpose:
        q = q.filter(Recipe.purpose == purpose)
    if flavor:
        q = q.filter(Recipe.flavor == flavor)
    if meal_time:
        q = q.filter(Recipe.meal_time == meal_time)
    return q


def list_posts(purpose=None, flavor=None, meal_time=None,
               sort='new', page=1, per_page=10):
    """广场列表（带三维度筛选 + 排序 + 分页）。

    sort: 'new' 按最新 / 'hot' 按点赞数。
    返回 (帖子列表, 总数, 当前页, 总页数) —— 页码越界自动收敛到有效范围，
    这样用户手改 URL 里的 page 也不会白屏。
    """
    q = _filtered(purpose, flavor, meal_time)
    if sort == 'hot':
        q = q.order_by(CommunityPost.like_count.desc(), CommunityPost.created_at.desc())
    else:
        q = q.order_by(CommunityPost.created_at.desc())

    total = q.count()
    pages = max(1, (int(total) + per_page - 1) // per_page)
    page = min(max(1, int(page or 1)), pages)
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    return items, total, page, pages


def list_posts_by_user(user_id):
    """「我的分享」：自己的帖子按时间倒序。"""
    return CommunityPost.query.filter_by(user_id=user_id) \
        .order_by(CommunityPost.created_at.desc()).all()


def related_posts(post, limit=3):
    """同类推荐：同一个「饮食目的」的其它帖子。没有目的标签时不给推荐。"""
    if not post.recipe or not post.recipe.purpose:
        return []
    return CommunityPost.query.join(Recipe, CommunityPost.recipe_id == Recipe.id) \
        .filter(Recipe.purpose == post.recipe.purpose,
                CommunityPost.id != post.id) \
        .order_by(CommunityPost.like_count.desc(), CommunityPost.created_at.desc()) \
        .limit(limit).all()


def find_share(user_id, recipe_id):
    """查某人是否已分享过某条食谱（详情页据此切换「分享」/「已分享」）。"""
    if not user_id or not recipe_id:
        return None
    return CommunityPost.query.filter_by(user_id=user_id, recipe_id=recipe_id).first()
