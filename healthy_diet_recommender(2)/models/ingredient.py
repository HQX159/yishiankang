# models/ingredient.py
from utils.database import db
from datetime import datetime


class Ingredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fridge_image_id = db.Column(db.Integer, db.ForeignKey('fridge_image.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    confidence = db.Column(db.Float)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)

    # fridge_image 关系由 FridgeImage 模型中的 backref 自动创建
    # fridge_image = db.relationship('FridgeImage', backref=db.backref('ingredients', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'fridge_image_id': self.fridge_image_id,
            'name': self.name,
            'confidence': self.confidence,
            'detected_at': self.detected_at.isoformat() if self.detected_at else None
        }