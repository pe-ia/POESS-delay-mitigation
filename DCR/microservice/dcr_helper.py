import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

DCR_API_BASE = "https://repository.dcrgraphs.net/api"
DCR_GRAPH_ID = os.getenv("DCR_GRAPH_ID")
USERNAME = os.getenv("DCR_USERNAME")
PASSWORD = os.getenv("DCR_PASSWORD")

# prepare Basic Auth header
auth_string = f"{USERNAME}:{PASSWORD}"
b64_auth = base64.b64encode(auth_string.encode()).decode()

HEADERS = {
    "Authorization": f"Basic {b64_auth}",
    "Content-Type": "application/json"
}

# only a few calls to dcrgraphs.net at the same time
few_at_a_time = threading.Semaphore(4)


def call(method, path, body=None, retry=False):
    """
    Return (status, headers, text). Never raises. retry repeats once after a network error.
    """
    result = call_once(method, path, body)
    if result[0] is None and retry:
        result = call_once(method, path, body)
    return result


def call_once(method, path, body):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{DCR_API_BASE}/graphs/{DCR_GRAPH_ID}{path}",
        data=data,
        method=method,
        headers=HEADERS,
    )
    with few_at_a_time:
        try:
            with urllib.request.urlopen(request, timeout=60) as reply:
                return reply.status, reply.headers, reply.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode("utf-8", "replace")
        except Exception as e:  # timeout, no network
            return None, {}, repr(e)


def create_case(start_time=None):
    """
    Start a new case. start_time (ISO 8601) sets the case clock. Returns the case id or None.
    """
    # retry=True: if the first try did reach the server we are left with one unused empty case, which is harmless
    status, headers, text = call("POST", "/sims", {"init": start_time} if start_time else {}, retry=True)
    if status not in (200, 201):
        print(f"[CreateCase] Error {status}: {text}", flush=True)
        return None
    return headers.get("simulationid")


def set_time(case_id, new_time):
    """
    Move the case clock to new_time (ISO 8601). The clock only moves when told to.
    """
    status, _, text = call("POST", f"/sims/{case_id}/time", {"time": new_time}, retry=True)
    return status in (200, 204), text


def execute_event(case_id, event_id, role=None, variable_type=None, value=None):
    """
    Execute one event. Returns (ok, error text).
    """
    body = {}
    if role:
        body["Role"] = role
    if variable_type:
        body["DataXML"] = (
            f'<globalStore><variable id="{event_id}" type="{variable_type}" '
            f'value="{value}" isNull="false"/></globalStore>'
        )
    path = f"/sims/{case_id}/events/{event_id}"
    status, _, text = call("POST", path, body)
    tries = 1
    while status is None and tries < 3:
        # No answer from dcrgraphs.net (a timeout). Try again.
        time.sleep(3)
        status, _, text = call("POST", path, body)
        tries += 1
        if status == 400 and "not executable" in text:
            # The try that got no answer did run after all, which is why it cannot run again.
            return True, ""
    return status in (200, 201, 204), "" if status in (200, 201, 204) else f"{status}: {text}"


def get_state(case_id):
    """
    Return {"enabled": [...], "pending": [...], "accepting": bool, "time": str} for a case.
    """
    status, _, text = call("GET", f"/sims/{case_id}/events?filter=all", retry=True)
    if status != 200:
        return {"enabled": [], "pending": [], "accepting": False, "time": ""}
    xml = text.replace('\\"', '"')
    events = [dict(re.findall(r'(\w+)="([^"]*)"', m)) for m in re.findall(r"<event ([^>]*)>", xml)]
    head = re.search(r"<events ([^>]*)>", xml).group(1)
    return {
        "enabled": [e["id"] for e in events if e.get("enabled") == "true"],
        "pending": [e["id"] for e in events if e.get("EffectivelyPending") == "true"],
        "accepting": bool(re.search(r'isAccepting\s*="True"', head)),
        "time": (re.search(r'currentTime\s*="([^"]*)"', head) or [None, ""])[1],
    }
