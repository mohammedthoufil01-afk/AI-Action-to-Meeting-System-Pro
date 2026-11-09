"""Development entry point: python run.py"""
import os

from app import create_app
from app.services.reminder_service import start_scheduler

app = create_app()

if __name__ == "__main__":
    debug = app.config["DEBUG"]
    # With the debug reloader two processes start; only start the scheduler in the serving one.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler(app)
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=debug)
