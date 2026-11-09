from .analytics import analytics_bp
from .auth import auth_bp
from .chatbot import chatbot_bp
from .dashboard import dashboard_bp
from .meetings import meetings_bp
from .tasks import tasks_bp

ALL_BLUEPRINTS = (auth_bp, dashboard_bp, meetings_bp, tasks_bp, analytics_bp, chatbot_bp)
