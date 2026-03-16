"""
V2 Web Routes — serves the single-page app shell for the intent-signal-first UI.
"""
from flask import Blueprint, render_template

web_bp = Blueprint('v2_web', __name__)


@web_bp.route('/app')
def v2_app():
    """Serve the v2 SPA shell."""
    return render_template('v2/app.html')


@web_bp.route('/writing-preferences')
def writing_preferences_page():
    """Serve the writing preferences page."""
    return render_template('v2/writing_preferences.html')
