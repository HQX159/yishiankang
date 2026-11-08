# models/fridge_image.py
from utils.database import db
from datetime import datetime


class FridgeImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # user 和 ingredients 关系由 User 模型中的 backref 自动创建
    # user = db.relationship('User', backref=db.backref('user_fridge_images', lazy=True))
    # ingredients = db.relationship('Ingredient', backref='fridge_image', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        # 获取关联的 ingredients
        from models.ingredient import Ingredient
        ingredients = Ingredient.query.filter_by(fridge_image_id=self.id).all()
        return {
            'id': self.id,
            'user_id': self.user_id,
            'filename': self.filename,
            'filepath': self.filepath,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'ingredients': [ingredient.to_dict() for ingredient in ingredients]
        }

# Note: The duplicate Ingredient class definition has been removed from this file.