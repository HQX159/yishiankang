# models/recipe.py
from utils.database import db
from datetime import datetime


class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    ingredients = db.Column(db.Text, nullable=False)  # 食材列表，用逗号分隔
    instructions = db.Column(db.Text, nullable=False)  # 烹饪步骤
    nutrition_info = db.Column(db.Text)  # 营养信息
    cooking_time = db.Column(db.Integer)  # 烹饪时间(分钟)
    servings = db.Column(db.Integer)  # 份量
    # R4 三维度标签。都可空 —— 本列上线前生成的老食谱没有这三个值，
    # 页面判断 recipe.purpose 为空就不显示标签（不要显示"—"占位）。
    # 取值定义见 utils/recipe_options.py（存英文 key，显示时翻译成中文）。
    purpose = db.Column(db.String(32))     # 饮食目的：控糖 / 控脂减重 / 降压护心 / 清淡养胃
                                           # （已下架的 muscle / balanced 仅存于历史数据，
                                           #   靠 recipe_options.LEGACY_LABELS 正常显示）
    flavor = db.Column(db.String(32))      # 饮食口味：清淡原味 / 咸鲜 / 酸辣 / 微甜 / 酱香 / 不挑
    meal_time = db.Column(db.String(32))   # 用餐时间：早餐 / 午餐 / 晚餐 / 加餐 / 夜宵
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # user 关系由 User 模型中的 backref 自动创建
    # user = db.relationship('User', backref=db.backref('user_recipes', lazy=True))

    # ── R4：给模板直接用的中文标签 ──────────────────────────────
    # 模板里写 {{ recipe.purpose_label }} 就行，不用在路由传一堆映射。
    # 三个属性在字段为空时都返回 ''（老食谱没这三个值 -> 页面不显示标签）。
    @property
    def purpose_label(self):
        from utils.recipe_options import label_of
        return label_of('purpose', self.purpose) if self.purpose else ''

    @property
    def flavor_label(self):
        from utils.recipe_options import label_of
        return label_of('flavor', self.flavor) if self.flavor else ''

    @property
    def meal_time_label(self):
        from utils.recipe_options import label_of
        return label_of('meal_time', self.meal_time) if self.meal_time else ''

    @property
    def has_tags(self):
        """有没有 R4 标签 —— 模板据此决定整行要不要渲染。"""
        return bool(self.purpose or self.flavor or self.meal_time)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'ingredients': self.ingredients,
            'instructions': self.instructions,
            'nutrition_info': self.nutrition_info,
            'cooking_time': self.cooking_time,
            'servings': self.servings,
            'purpose': self.purpose,
            'purpose_label': self.purpose_label,
            'flavor': self.flavor,
            'flavor_label': self.flavor_label,
            'meal_time': self.meal_time,
            'meal_time_label': self.meal_time_label,
            'created_at': self.created_at.isoformat()
        }


class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # recipe 关系由 Recipe 模型中的 backref 自动创建
    # recipe = db.relationship('Recipe', backref=db.backref('favorited_by', lazy=True))

    __table_args__ = (db.UniqueConstraint('user_id', 'recipe_id', name='unique_user_recipe'),)