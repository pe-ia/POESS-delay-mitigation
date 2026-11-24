# POESS Example

This repository contains two minimal reference implementations for process-oriented and event-driven software systems:

- a BPMN-based setup built with Camunda Zeebe, Siddhi, and Python
- a DCR-based setup built with DCR Graph, Siddhi, and Python

The goal is to give students a lightweight starting point for exploring workflow automation, event processing, and integration patterns in a hands-on way.

> The BPMN and DCR projects are separate implementations. They are designed to be copied, modified, and extended independently.

---

## Repository structure

```text
POESS-example/
├── BPMN/
│   ├── bpmn/
│   ├── deployer/
│   ├── event_streams/
│   ├── logging_service/
│   ├── siddhi/
│   ├── docker-compose.yml
│   └── README.md
├── DCR/
│   ├── logging_service/
│   ├── microservice/
│   ├── siddhi/
│   ├── docker-compose.yml
│   ├── README.md
│   └── rebuild.sh
├── DevTools/
│   └── docker-compose.yml
├── README.md
└── .gitignore
```

---

## BPMN project

The BPMN implementation focuses on process orchestration using Camunda Zeebe together with a Siddhi-based event layer and Python service components.

Key characteristics:

- order processing modeled as a BPMN workflow
- Zeebe as the workflow engine
- Siddhi handling event routing and processing
- Python deployment services for communication and integration
- logging and process tracking for debugging and inspection

For the BPMN-specific setup and commands, see [BPMN/README.md](BPMN/README.md).

---

## DCR project

The DCR implementation represents an event-driven workflow using DCR Graph semantics and stream processing. It is built around the idea of validating and routing order-related actions through a central event processor.

Key characteristics:

- DCR-based constraints and event handling
- Siddhi runtime for event orchestration
- Python microservice for interaction with the DCR backend
- logging service for recording operational issues

For the DCR-specific setup and commands, see [DCR/README.md](DCR/README.md).

---

## Prerequisites

To run either stack locally, you need:

- Docker Engine or Docker Desktop
- Docker Compose
- VS Code (recommended for development)
- A working local environment capable of building and running containers

For Siddhi development, we recommend using the local editor setup included in the DevTools folder. This provides a lightweight local environment for editing and testing Siddhi files in a way that fits the project setup.

The `DevTools/docker-compose.yml` configuration is the preferred option for this repository.

If you prefer to work with Siddhi directly in VS Code, the WSO2 Siddhi extension is also available here:

- https://marketplace.visualstudio.com/items?itemName=WSO2.streaming-integrator

But notice, that from our experience the DevTools we provice gives much clearer error messages.

---

## Running the examples

From the project root, navigate into the implementation you want to run:

```bash
cd BPMN
docker compose up --build
```

or:

```bash
cd DCR
docker compose up --build
```

To stop the services:

```bash
docker compose down
```

---

## Development notes

- The BPMN and DCR folders are intentionally separate and can be used independently.
- The repository is intended as a foundation for student projects and course examples.
- The design is intentionally minimal so it can be extended with more complex workflows, services, and event patterns.

---

## Useful links

- [BPMN/README.md](BPMN/README.md)
- [DCR/README.md](DCR/README.md)
- [BPMN/event_streams/README.md](BPMN/event_streams/README.md)

---

## Changes from the course example

Starting point: `POESS-example/DCR` (https://github.com/AndersenChristian/POESS-example).
We kept its design and rewrote its contents for P5. The changes were made with the help of an AI companion (Claude Fable 5.2), based on my instructions and design decisions.

### Kept from the example

- Three Docker containers: Siddhi, a Python microservice, a logging service.
- Siddhi takes events over HTTP and posts to the microservice.
- The microservice runs events on dcrgraphs.net through its REST API, and reports every result back to Siddhi (both directions between PAIS and CEP).
- The logging service turns what Siddhi forwards into an XES event log.
- The Dockerfiles, `rebuild.sh` and the folder layout.

### Changed, part by part

`DCR/siddhi/P5DelayMitigation.siddhi` (new, replaces the food-order app)

- Keeps the station boards and each trip's remaining stops in tables.
- Stands in for P4: a trip 5 or more minutes late gets a P5 case with a band.
- Sends delay updates, and "Trip ended" when a trip leaves the boards.
- Works out which trains and TPG connections are at risk.
- Applies the hold rule from `P5_hold_rule.dmn`.
- Runs the automatic events: route read, checks, action record.

`DCR/microservice/dcr_helper.py` (rewritten)

- Sends a variable's value with an event (band, delay, true / false answers).
- Sets the case clock to the event's own time, so a replay keeps its times.
- Tries a call again when dcrgraphs.net does not answer.

`DCR/microservice/app.py` (rewritten)

- Two general endpoints, start a case and run any event, in place of one endpoint per food-order action.
- Runs the events of one case one after the other. dcrgraphs.net loses an event's effects when two events of the same case run at the same moment.
- Answers Siddhi at once and does the slow work in the background.

`DCR/microservice/events.json` (new, generated by `DCR/tools/build.py`)

- Each event's id on dcrgraphs.net with its label, role and variable.

`DCR/logging_service/` (rewritten)

- Writes the XES the way Process DoCtoR reads it: the event's label as `concept:name`, a role, a timestamp, and the variable with its type.
- Failed events go to `logs/errors.log`, not into the XES.

`DCR/replay/replay.py` (new)

- Feeds one day of scraped Geneva boards to Siddhi at the pace of the data.
- Plays the dispatcher, the drivers and SBB / LEX. Some react late on purpose, so the conformance check has something to find.

`DCR/tools/build.py` (new)

- Makes `events.json` from the model file `P5_delay_mitigation_timed.xml`.

`DMN/check_dmn.py` (new)

- Tests a DMN decision table in Camunda Zeebe 8.8.0, the engine the BPMN example runs: `python3 DMN/check_dmn.py <dmn> <cases csv> <log>`.
- Starts the engine in one container, deploys the table, evaluates every case in the csv (the first row names the inputs, every other row is one case) and removes the container again.
- Writes every request and the engine's answer to the log.
- We tested the hold rule table `P5_hold_rule.dmn` with it.

### Not in this repo

- The DCR graphs themselves. We modelled them in Process DoCtoR, and the engine's copy on dcrgraphs.net was rebuilt there by hand.
- The hold rule table `P5_hold_rule.dmn` and its cases file. `DMN/check_dmn.py` takes their paths as arguments.
