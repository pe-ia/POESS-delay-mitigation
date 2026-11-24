"""Evaluate a DMN decision table in Camunda Zeebe for every case in a csv file and write the engine's answers to a log."""
import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.dom.minidom as minidom

IMAGE = "camunda/zeebe:8.8.0"          # the engine and version of BPMN/docker-compose.yml
CONTAINER = "p5-zeebe-dmn-check"
PORT = 8089
BASE = f"http://localhost:{PORT}"
ENV = {
    "CAMUNDA_SECURITY_AUTHENTICATION_UNPROTECTEDAPI": "true",   # as in BPMN/docker-compose.yml
    "CAMUNDA_SECURITY_AUTHORIZATIONS_ENABLED": "false",        # as in BPMN/docker-compose.yml
    "CAMUNDA_DATA_SECONDARYSTORAGE_TYPE": "none",              # no Elasticsearch: deploying and evaluating need no database
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate a DMN decision table in Camunda Zeebe for every case in a csv file."
    )
    parser.add_argument("dmn", help="path to the .dmn file with one decision table")
    parser.add_argument("cases", help="path to the cases csv: the first row names the inputs, every other row is one case")
    parser.add_argument("log", help="where to write the log: every request and the engine's answer (overwritten)")
    return parser.parse_args()


def value(text):
    """A csv cell as the engine should get it: number, true / false, or text."""
    text = text.strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def call(method, path, body=None, content_type=None):
    """One request to the engine. Returns the answer's body exactly as sent, also for an error answer."""
    request = urllib.request.Request(BASE + path, data=body, method=method)
    if content_type:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def wait_for_engine():
    for _ in range(90):
        try:
            status, answer = call("GET", "/v2/topology")
            if status == 200 and '"health":"healthy"' in answer:
                return answer
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    sys.exit("the engine did not come up within 3 minutes")


def form_with_file(path):
    """The dmn file as a multipart form with the field "resources", as the deploy request wants it."""
    boundary = "----check-dmn-boundary"
    name = path.replace("\\", "/").split("/")[-1]
    with open(path, "rb") as f:
        content = f.read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="resources"; filename="{name}"\r\n'
            f"Content-Type: application/xml\r\n\r\n").encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def main():
    args = parse_args()
    decisions = minidom.parse(args.dmn).getElementsByTagNameNS("*", "decision")
    assert len(decisions) == 1, f"the dmn file has {len(decisions)} decisions, this tool expects one"
    decision_id = decisions[0].getAttribute("id")
    with open(args.cases, encoding="utf-8", newline="") as f:
        rows = [row for row in csv.reader(f) if row]
    names, cases = [n.strip() for n in rows[0]], rows[1:]
    assert cases, "the cases file has no case below its first row"

    existing = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{CONTAINER}$", "-q"], capture_output=True, text=True)
    if existing.returncode != 0:
        sys.exit("docker does not answer: " + existing.stderr.strip())
    if existing.stdout.strip():
        sys.exit(f'a container named "{CONTAINER}" exists already, remove it first: docker rm -f {CONTAINER}')

    run = ["docker", "run", "-d", "--name", CONTAINER, "-p", f"{PORT}:8080"]
    for key, setting in ENV.items():
        run += ["-e", f"{key}={setting}"]
    started = subprocess.run(run + [IMAGE], capture_output=True, text=True)
    if started.returncode != 0:
        sys.exit("could not start the engine: " + started.stderr.strip())

    refused = 0
    try:
        with open(args.log, "w", encoding="utf-8") as log:
            log.write(f"GET {BASE}/v2/topology\n{wait_for_engine()}\n\n")
            body, content_type = form_with_file(args.dmn)
            status, answer = call("POST", "/v2/deployments", body, content_type)
            log.write(f"POST {BASE}/v2/deployments resources=@{args.dmn}\n{answer}\n")
            if status != 200:
                sys.exit(f"the engine refused the dmn file (http {status}), see {args.log}")
            for case in cases:
                request = json.dumps({"decisionDefinitionId": decision_id, "variables": dict(zip(names, map(value, case)))},
                                     separators=(",", ":"))
                status, answer = call("POST", "/v2/decision-definitions/evaluation", request.encode("utf-8"), "application/json")
                refused += status != 200
                log.write(f"\nPOST {BASE}/v2/decision-definitions/evaluation {request}\n{answer}\n")
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    print(f"decision {decision_id}: {len(cases)} cases sent, {refused} refused by the engine, log written to {args.log}")
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()
