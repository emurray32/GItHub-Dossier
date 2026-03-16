"""
V2 Web Routes — serves the single-page app shell for the intent-signal-first UI.
"""
from flask import Blueprint, render_template, make_response

web_bp = Blueprint('v2_web', __name__)


@web_bp.route('/app')
def v2_app():
    """Serve the v2 SPA shell."""
    resp = make_response(render_template('v2/app.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@web_bp.route('/writing-preferences')
def writing_preferences_page():
    """Serve the writing preferences page."""
    return render_template('v2/writing_preferences.html')
