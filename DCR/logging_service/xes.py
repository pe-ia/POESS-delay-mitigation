import xml.etree.ElementTree as ET

XES_TAG = {"int": "int", "bool": "boolean", "string": "string"}


def build_xes(events, path):
    """
    events: list of dicts as stored by app.py. Failed executions are left out.
    """
    root = ET.Element("log", {"xes.version": "1.0", "xes.features": "nested-attributes"})
    ET.SubElement(root, "extension", {"name": "Concept", "prefix": "concept", "uri": "http://www.xes-standard.org/concept.xesext"})
    ET.SubElement(root, "extension", {"name": "Time", "prefix": "time", "uri": "http://www.xes-standard.org/time.xesext"})
    ET.SubElement(root, "classifier", {"name": "Event Name", "keys": "concept:name"})

    traces = {}
    for event in events:
        if event.get("error") or not event.get("case_id"):
            continue
        traces.setdefault(event["case_id"], []).append(event)

    for case_id, case_events in traces.items():
        trace = ET.SubElement(root, "trace")
        ET.SubElement(trace, "string", {"key": "concept:name", "value": str(case_id)})
        if case_events[0].get("trip_id"):
            ET.SubElement(trace, "string", {"key": "trip_id", "value": str(case_events[0]["trip_id"])})
        for event in case_events:
            el = ET.SubElement(trace, "event")
            ET.SubElement(el, "string", {"key": "concept:name", "value": event["label"]})
            ET.SubElement(el, "string", {"key": "role", "value": event.get("role", "")})
            ET.SubElement(el, "date", {"key": "time:timestamp", "value": event["timestamp"]})
            if event.get("variable") and event.get("var_type") in XES_TAG:
                ET.SubElement(el, XES_TAG[event["var_type"]], {"key": event["variable"], "value": str(event["value"])})

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
