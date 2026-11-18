# P5 engine

Runs P5 Delay Impact Mitigation. Adapted from POESS-example/DCR.

- dcrgraphs.net is the DCR engine. The graph it runs is given to the microservice with --graph-id (python app.py --graph-id <id>), or as DCR_GRAPH_ID in microservice/.env when no argument is passed. It was rebuilt there by hand from the model file P5_delay_mitigation_timed.xml
- siddhi/ receives events, keeps the trip -> case table, and routes
- microservice/ runs events on dcrgraphs.net and reports each one back to Siddhi
- logging_service/ writes logging_service/XES/process_log.xes for processdoctor

## Run

    docker compose up --build

The login is in microservice/.env (see .env.template). Leave .env out of source.zip.

## Replay the scraped data

    docker compose restart siddhi        # empties Siddhi's tables, do this before every replay
    rm -f logging_service/XES/events.json logging_service/XES/process_log.xes logging_service/logs/errors.log
    python3 replay/replay.py --data <folder> --from 16:00 --to 17:00

--data is the folder with the collector's raw-*.jsonl.gz files. The script sends the Geneva boards to Siddhi as the pollers would,
and plays the dispatcher, the drivers and SBB / LEX. Their reaction times and yes / no
decisions are random but repeatable (--seed), and some reactions are slower than the model's
deadlines on purpose, so the log has late cases for the conformance check to find.
--punctual makes everyone act at once. --lines 10,D opens cases for those TPG lines only.

## What Siddhi does (siddhi/P5DelayMitigation.siddhi)

1. Keeps every departure and every trip's remaining stops in tables.
2. Stands in for P4: a TPG trip 5 or more minutes late gets a case with band significant, 10 or more severe. A minor delay (3 or 4 minutes) stays with P4. Later changes of the delay become "Delay update (stop.delay)".
3. When the case has started it runs "Read remaining route", then the train check and the TPG check, then "Evaluate TPG hold rule" (the table in P5_hold_rule.dmn) if a TPG connection is at risk. The checks run again when the delay gets 3 or more minutes worse.
4. After every mitigation action it runs "Send action record to event processing".
5. Actions by people come in on port 7073 and are passed to the engine.
6. A trip that is on no monitored board any more gets "Trip ended", so its case can close.

A connection counts as planned when it leaves 2 to 10 minutes after the trip's scheduled arrival at a stop. The trip misses it when, with its delay and 2 minutes to change, it gets there after the connection's expected departure. A missed train is at risk. A missed TPG connection is at risk when a hold of 5 minutes or less would save it and its line runs every 15 minutes or less often.

## The model's files

There is one model file, P5_delay_mitigation_timed.xml. It is not in this repo. It is the one in the report and the
one the logs are checked against in processdoctor. After editing it, run

    python3 tools/build.py <path to the model xml>    # writes microservice/events.json and checks the file

Its three nestings have ids starting with Group_, because processdoctor switches its detailed
analysis off for any model file that contains the word "Nesting".
The graph on dcrgraphs.net is a separate copy and has to be changed by hand to match.

## Send events by hand

A delay reading:

    curl -X POST localhost:7071/delay -H 'Content-Type: application/json' \
      -d '{"trip_id":"T1","delay":12,"timestamp":"2026-10-23T16:00:00+02:00"}'

An action, by event id (see microservice/events.json). value and role may be empty:

    curl -X POST localhost:7073/action -H 'Content-Type: application/json' \
      -d '{"trip_id":"T1","event_id":"Event_ASSESS","value":"","role":"","timestamp":"2026-10-23T16:01:00+02:00"}'

## Notes

- A case's clock only moves when an event arrives. It is set to that event's timestamp, so a replay keeps its own times and the deadlines are checked against them.
- Event ids: an event with a variable uses the variable name (band, delay, railAtRisk, ...), the others use Event_... ids.
- P4 is not implemented. Siddhi stands in for it with the limits of P4's decision table (5 and 10 minutes).
- dcrgraphs.net loses an event's effects when two events of one case run at once, so the microservice runs each case's events one after the other.
- A call to dcrgraphs.net sometimes times out. It is tried up to three times.
