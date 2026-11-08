# utils/file_handler.py
import os
from werkzeug.utils import secure_filename
from config import config

ALLOWED_EXTENSIONS_HEALTH_REPORTS = {'pdf', 'png', 'jpg', 'jpeg'}
ALLOWED_EXTENSIONS_FRIDGE_IMAGES = {'png', 'jpg', 'jpeg'}

def allowed_file_health_report(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_HEALTH_REPORTS

def allowed_file_fridge_image(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_FRIDGE_IMAGES

def save_uploaded_file(file, upload_folder):
    """Generic function to save an uploaded file."""
    if file:
        filename = secure_filename(file.filename)
        # Ensure unique filename if needed, here just using secure one
        filepath = os.path.join(upload_folder, filename)
        file.save(filepath)
        return filepath, filename
    return None, None