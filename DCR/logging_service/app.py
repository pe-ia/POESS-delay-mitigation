import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

from xes import build_xes

app = Flask(__name__)

LOG_DIR = Path("/logs")
XES_DIR = Path("/XES")
LOG_DIR.mkdir(parents=True, exist_ok=True)
XES_DIR.mkdir(parents=True, exist_ok=True)

EVENT_STORE = XES_DIR / "events.json"
XES_FILE = XES_DIR / "process_log.xes"
ERROR_LOG = LOG_DIR / "errors.log"

FIELDS = ["trip_id", "case_id", "event", "label", "variable", "var_type", "value", "timestamp", "role", "payload", "error"]
LOCK = threading.Lock()  # requests arrive in parallel, the files are rewritten whole


@app.route("/log", methods=["POST"])
def log_event():
    data = request.get_json(silent=True) or {}
    if isinstance(data.get("event"), dict):  # Siddhi's JSON sink wraps the fields in {"event": {...}}
        data = data["event"]
    if not data:
        return jsonify({"status": "invalid"}), 400

    event = {field: str(data.get(field) or "") for field in FIELDS}
    event["timestamp"] = event["timestamp"] or datetime.now(timezone.utc).isoformat()
    event["label"] = event["label"] or event["event"]

    with LOCK:
        try:
            events = json.loads(EVENT_STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            events = []
        events.append(event)
        tmp = EVENT_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(EVENT_STORE)  # readers never see a half-written file
        build_xes(events, XES_FILE)

    if event["error"]:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"{event['timestamp']} trip={event['trip_id']} case={event['case_id']} {event['event']}: {event['error']}\n")
    print(f"[XES] Logged event={event['label']} case_id={event['case_id']} ts={event['timestamp']}", flush=True)
    return jsonify({"status": "logged"}), 200


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5010, threads=16)
