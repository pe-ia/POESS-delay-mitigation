import argparse
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Flask, jsonify, request

import dcr_helper
from dcr_helper import create_case, execute_event, get_state, set_time

app = Flask(__name__)

SIDDHI_CALLBACK_URL = os.getenv("SIDDHI_CALLBACK_URL", "http://siddhi:7072/middleman")
START_EVENT = "band"  # "Receive delay case from P4"

# id -> label, role, variable, type. Generated from P5_delay_mitigation_timed.xml.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json"), encoding="utf-8") as f:
    EVENTS = json.load(f)


# Siddhi gets its answer at once and the DCR calls run in the background.
# dcrgraphs.net loses an event's effects when two events of one case run at the
# same moment, so each case has its own queue with a single worker.
_queues = {}


def queue_for(key):
    if key not in _queues:
        _queues[key] = ThreadPoolExecutor(max_workers=1)
    return _queues[key]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def body():
    """
    Siddhi's JSON sink wraps the fields in {"event": {...}}. Accept both shapes.
    """
    data = request.get_json(silent=True) or {}
    return data.get("event", data) if isinstance(data.get("event", data), dict) else data


def callback(trip_id, case_id, event_id, value, role, timestamp, error):
    """
    Tell Siddhi what happened.
    """
    info = EVENTS.get(event_id, {})
    state = get_state(case_id) if case_id else {}
    payload = {
        "trip_id": trip_id,
        "case_id": case_id or "",
        "event": event_id,
        "label": info.get("label", event_id),
        "variable": info.get("variable") or "",
        "var_type": info.get("type") or "",
        "value": "" if value is None else str(value),
        "timestamp": timestamp,
        "role": role,
        "payload": json.dumps(state),
        "error": error,
    }
    req = urllib.request.Request(
        SIDDHI_CALLBACK_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"[Callback] Could not reach Siddhi: {e!r}", flush=True)
    return payload


def run_event(trip_id, case_id, event_id, value, role, timestamp):
    info = EVENTS.get(event_id)
    if info is None:
        return callback(trip_id, case_id, event_id, value, role, timestamp, f"unknown event id {event_id}")
    role = role or info["role"]
    if info["type"] == "bool":
        value = str(value).strip().lower()
    set_time(case_id, timestamp)  # refused if it would move the clock back, which is fine
    ok, error = execute_event(case_id, event_id, role, info["type"], value if info["type"] else None)
    return callback(trip_id, case_id, event_id, value if info["type"] else None, role, timestamp, error)


def open_case(trip_id, band, timestamp):
    case_id = create_case(timestamp)
    if case_id is None:
        callback(trip_id, None, START_EVENT, band, "", timestamp, "could not create case")
    else:
        queue_for(case_id).submit(run_event, trip_id, case_id, START_EVENT, band, None, timestamp)


@app.route("/start_case", methods=["POST"])
def start_case():
    """
    Body: trip_id, band, timestamp. Creates a case and executes "Receive delay case from P4".
    """
    data = body()
    queue_for("trip " + str(data.get("trip_id"))).submit(
        open_case, data.get("trip_id"), data.get("band"), data.get("timestamp") or utc_now_iso())
    return jsonify({"status": "queued"}), 202


@app.route("/execute", methods=["POST"])
def execute():
    """
    Body: trip_id, case_id, event_id, value (may be empty), role (may be empty), timestamp.
    """
    data = body()
    queue_for(data.get("case_id")).submit(
        run_event, data.get("trip_id"), data.get("case_id"), data.get("event_id"),
        data.get("value"), data.get("role"), data.get("timestamp") or utc_now_iso())
    return jsonify({"status": "queued"}), 202


@app.route("/state/<case_id>", methods=["GET"])
def state(case_id):
    return jsonify(get_state(case_id))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run P5 events on dcrgraphs.net for Siddhi."
    )
    parser.add_argument(
        "--graph-id",
        default=os.getenv("DCR_GRAPH_ID"),
        help="id of the graph on dcrgraphs.net (default: the DCR_GRAPH_ID environment variable)",
    )
    args = parser.parse_args()
    if not args.graph_id:
        parser.error("no graph id: pass --graph-id or set DCR_GRAPH_ID")
    return args


if __name__ == "__main__":
    args = parse_args()
    dcr_helper.DCR_GRAPH_ID = args.graph_id

    from waitress import serve
    serve(app, host="0.0.0.0", port=5001, threads=32)
