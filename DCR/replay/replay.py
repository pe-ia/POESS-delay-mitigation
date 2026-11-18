"""Replay scraped Geneva station boards into Siddhi, with stand-ins for the people."""
import argparse
import glob
import gzip
import json
import os
import random
import threading
import time
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
EVENT_LOG = os.path.join(HERE, "..", "logging_service", "XES", "events.json")
EVENTS = json.load(open(os.path.join(HERE, "..", "microservice", "events.json"), encoding="utf-8"))

SIDDHI = "http://localhost"
DEPARTURE = f"{SIDDHI}:7074/departure"
ROUTE = f"{SIDDHI}:7075/route"
ACTION = f"{SIDDHI}:7073/action"
ENDED = f"{SIDDHI}:7076/trip_ended"

TRAINS = "8501008"  # Genève (rail station)
# The Geneva stops the collector watches. Connections are only known at these.
STOPS = {TRAINS, "8587057", "8592801", "8592812", "8587387", "8587907", "8587061"}
GONE_AFTER = timedelta(minutes=2)   # a trip off every board this long has ended or left the area
PEOPLE = {"Dispatcher", "Driver", "SBB / LEX"}
CLOSE_WAIT = timedelta(minutes=2, seconds=5)  # the model lets a case close 2 minutes after it starts

# Reaction time in seconds, as (shortest, most likely, longest). The model's
# deadlines: assess 2 min, SBB / LEX reply 3 min, release a held vehicle 5 min.
REACTION = {
    "Event_ASSESS": (15, 50, 200),
    "holdAccepted": (30, 90, 300),
    "Event_RELT": (60, 150, 420),
    "Event_REL": (60, 150, 420),
    "Event_RIS": (240, 480, 900),
}
REACTION_DEFAULT = (5, 20, 75)
# Chance of a yes for each decision.
YES = {"requestHold": 0.8, "holdAccepted": 0.5, "holdApproved": 0.8}

clock = None            # current time in the replay
clock_lock = threading.Lock()


def post(url, body):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except Exception as e:
        print(f"  ! {url}: {e!r}")


def now():
    with clock_lock:
        return clock


def read_records(data, start, end):
    """
    Board snapshots of the Geneva stops between start and end, oldest first.
    """
    records = []
    for path in sorted(glob.glob(os.path.join(data, "**", "raw-*.jsonl.gz"), recursive=True)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                at = datetime.fromisoformat(record["fetched_at"])
                if record["stop_id"] in STOPS and "response" in record and start <= at.time() < end:
                    records.append((at, record))
    return sorted(records, key=lambda r: r[0])


def feed(records, start, speed, lines):
    """
    Send what changed on each board. Snapshots before start only fill Siddhi's
    tables (they go as fast as possible and open no cases). Returns the tracked trips.
    """
    global clock
    last_delay = {}     # (stop, trip) -> delay last sent
    last_seen = {}      # tracked trip -> replay time it was last on a board
    ended = set()
    first, began = None, None

    for at, record in records:
        live = at.time() >= start
        if live:
            if first is None:
                first, began = at, time.time()
            time.sleep(max((at - first).total_seconds() / speed - (time.time() - began), 0))
        with clock_lock:
            clock = at
        stamp, stop_id = at.isoformat(timespec="seconds"), record["stop_id"]

        for entry in record["response"]["stationboard"]:
            stop, tpg = entry["stop"], entry["category"] in ("B", "T")
            if not stop.get("departureTimestamp"):
                continue
            trip_id = f"{entry['number']}-{entry['name']}"
            delay = stop.get("delay")
            track = live and tpg and delay is not None and (not lines or str(entry["number"]) in lines)
            if track:
                last_seen[trip_id] = at
                ended.discard(trip_id)

            key = (stop_id, trip_id)
            if key not in last_delay and tpg:
                # first time on this board: its remaining monitored stops, this one included
                post(ROUTE, {"trip_id": trip_id, "stop_id": stop_id, "sched_arr": stop["departureTimestamp"] * 1000, "timestamp": stamp})
                for later in entry["passList"][1:]:
                    if later["station"]["id"] in STOPS and later.get("arrivalTimestamp"):
                        post(ROUTE, {"trip_id": trip_id, "stop_id": later["station"]["id"],
                                     "sched_arr": later["arrivalTimestamp"] * 1000, "timestamp": stamp})
            if last_delay.get(key, "new") != (delay, track):
                last_delay[key] = (delay, track)
                post(DEPARTURE, {"stop_id": stop_id, "kind": "tpg" if tpg else "train", "line": str(entry["number"]),
                                 "dest": entry["to"] or "", "trip_id": trip_id,
                                 "sched_dep": stop["departureTimestamp"] * 1000,
                                 "delay": delay or 0, "track": track, "timestamp": stamp})

        for trip_id, seen in last_seen.items():
            if trip_id not in ended and at - seen >= GONE_AFTER:
                ended.add(trip_id)
                post(ENDED, {"trip_id": trip_id, "timestamp": stamp})

    return last_seen, ended


def read_log():
    try:
        with open(EVENT_LOG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def cases(log):
    """
    case id -> {"trip", "open", "state", "values", "done", "started"} from the log.
    """
    result = {}
    for event in log:
        if not event["case_id"]:
            continue
        case = result.setdefault(event["case_id"], {"trip": event["trip_id"], "open": True, "state": {}, "values": {},
                                                    "done": set(), "started": datetime.fromisoformat(event["timestamp"])})
        try:
            case["state"] = json.loads(event["payload"]) or case["state"]
        except ValueError:
            pass
        if not event["error"]:
            case["done"].add(event["event"])
            if event["variable"]:
                case["values"][event["variable"]] = event["value"]
            if event["event"] == "Event_CLOSE":
                case["open"] = False
    return result


def act(stop, rng, punctual):
    """
    Until told to stop: do what each open case is waiting for, after a reaction time.
    """
    due = {}    # (case, event, results in the log) -> replay time the stand-in acts
    sent = set()
    while not stop.is_set():
        log = read_log()
        results = {}
        for event in log:
            results[(event["case_id"], event["event"])] = results.get((event["case_id"], event["event"]), 0) + 1
        for case_id, case in cases(log).items():
            if not case["open"]:
                continue
            enabled, pending = set(case["state"].get("enabled", [])), set(case["state"].get("pending", []))
            wanted = [e for e in enabled & pending if EVENTS[e]["role"] in PEOPLE]
            # a replacement bus is optional: the dispatcher sends one once in a severe case
            if "Event_REPL" in enabled and "Event_REPL" not in case["done"] and case["values"].get("band") == "severe":
                wanted.append("Event_REPL")
            # Close is the only thing left but the model's 2-minute wait blocked it when the
            # state was last read: the wait is over once the clock has moved on, so try it
            if pending == {"Event_CLOSE"} and "Event_CLOSE" not in enabled and now() - case["started"] >= CLOSE_WAIT:
                wanted.append("Event_CLOSE")
            for event_id in wanted:
                key = (case_id, event_id, results.get((case_id, event_id), 0))
                if key in sent:
                    continue        # on its way: its result is not in the log yet
                if key not in due:
                    low, mode, high = REACTION.get(event_id, REACTION_DEFAULT)
                    due[key] = now() + timedelta(seconds=0 if punctual else rng.triangular(low, high, mode))
                if now() < due[key]:
                    continue
                sent.add(key)
                value = ""
                if event_id in YES:
                    value = "true" if punctual or rng.random() < YES[event_id] else "false"
                post(ACTION, {"trip_id": case["trip"], "event_id": event_id, "value": value,
                              "role": EVENTS[event_id]["role"], "timestamp": now().isoformat(timespec="seconds")})
        stop.wait(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Replay scraped Geneva station boards into Siddhi."
    )
    parser.add_argument("--data", required=True, help="folder with the collector's raw-*.jsonl.gz files")
    parser.add_argument("--from", dest="start", required=True, help="HH:MM, start of the window")
    parser.add_argument("--to", dest="end", required=True, help="HH:MM, end of the window")
    parser.add_argument("--speed", type=float, default=3, help="replay this many times faster than real time")
    parser.add_argument("--lines", default="", help="only open cases for these TPG lines, e.g. 10,D (default: all)")
    parser.add_argument("--warmup", type=int, default=15, help="minutes of boards to load before the window, so headways are known")
    parser.add_argument("--settle", type=int, default=600, help="seconds to let open cases finish after the feed ends")
    parser.add_argument("--seed", type=int, default=1, help="seed for the stand-ins' reaction times and decisions")
    parser.add_argument("--punctual", action="store_true", help="stand-ins act at once and always say yes")
    return parser.parse_args()


def main():
    global clock
    args = parse_args()
    start, end = (datetime.strptime(t, "%H:%M").time() for t in (args.start, args.end))
    before = (datetime.combine(datetime.today(), start) - timedelta(minutes=args.warmup)).time()
    lines = {l for l in args.lines.split(",") if l}

    records = read_records(args.data, before, end)
    live = [at for at, _ in records if at.time() >= start]
    if not live:
        raise SystemExit("no snapshots in that window")
    clock = records[0][0]
    print(f"{len(live)} snapshots, {live[0]:%H:%M:%S} to {live[-1]:%H:%M:%S}, speed x{args.speed:g}, "
          f"{len(records) - len(live)} more loaded first as warm-up")

    stop = threading.Event()
    actors = threading.Thread(target=act, args=(stop, random.Random(args.seed), args.punctual), daemon=True)
    actors.start()

    last_seen, ended = feed(records, start, args.speed, lines)

    # The window is over: the trips still on a board end here too, so their cases can close.
    # First give cases that are just being opened the time to get their id.
    time.sleep(15)
    stamp = now().isoformat(timespec="seconds")
    for trip_id in last_seen:
        if trip_id not in ended:
            post(ENDED, {"trip_id": trip_id, "timestamp": stamp})

    # the clock keeps running at the same speed while the open cases finish
    last, finished, deadline = records[-1][0], time.time(), time.time() + args.settle
    while time.time() < deadline and any(c["open"] for c in cases(read_log()).values()):
        time.sleep(1)
        with clock_lock:
            clock = last + timedelta(seconds=(time.time() - finished) * args.speed)
    stop.set()

    log, summary = read_log(), cases(read_log())
    print(f"cases opened {len(summary)}, closed {sum(not c['open'] for c in summary.values())}, "
          f"events {sum(not e['error'] for e in log)}, refused {sum(bool(e['error']) for e in log)}")


if __name__ == "__main__":
    main()
