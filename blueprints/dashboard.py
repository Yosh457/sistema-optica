# blueprints/dashboard.py
from flask import Blueprint, render_template
from flask_login import login_required

dashboard_bp = Blueprint('dashboard', __name__, template_folder='../templates')

@dashboard_bp.route('/dashboard')
@login_required
def index():
    return render_template('dashboard/index.html')