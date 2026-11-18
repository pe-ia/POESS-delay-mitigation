"""Derive microservice/events.json from the model file."""
import argparse
import json
import os
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "microservice", "events.json")
TYPES = {"Int": "int", "Bool": "bool", "String": "string"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Derive microservice/events.json from the model file."
    )
    parser.add_argument("model", help="path to the model xml exported from Process DoCtoR")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the event list (default: microservice/events.json)")
    return parser.parse_args()


def main():
    args = parse_args()
    with open(args.model, encoding="utf-8") as f:
        # processdoctor switches its detailed conformance analysis off for a model file that contains this word
        assert "Nesting" not in f.read(), 'the model file contains the word "Nesting", give its nestings ids without it'
    doc = minidom.parse(args.model)
    table = {}
    for e in doc.getElementsByTagName("dcr:event"):
        data = e.getElementsByTagName("dcr:eventData")
        name = data[0].getAttribute("name") if data else None
        kind = data[0].getAttribute("type") if data else None
        # on dcrgraphs.net an event with a variable has the variable's name as its id
        table[name or e.getAttribute("id")] = {"label": e.getAttribute("description"), "role": e.getAttribute("role"),
                                               "variable": name, "type": TYPES.get(kind)}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)
    print(f"{len(table)} events, {len(doc.getElementsByTagName('dcr:relation'))} relations, "
          f"{len(doc.getElementsByTagName('dcr:nesting'))} nestings")


if __name__ == "__main__":
    main()
