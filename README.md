# NetWatch Lite

Know when your site, server or disk is in trouble before someone tells you. It checks your own services on a schedule, keeps a history, and shows a simple status page. It needs nothing but Python.

```
python -m netwatch_lite
```

Then open http://127.0.0.1:8050 and add a check. Keep it running to keep checking.

## What it checks

- **Web pages:** the status code, and optionally that some text is on the page, because a page can return 200 and still be broken
- **TCP ports:** for mail, databases or SSH
- **Disk space:** a warning below a set share free

## What it tells you

- **State:** up, flaky (some failures, no outage yet) or down
- **Outages:** one failed check is often a blip, so an outage opens after three failures in a row, and its start is dated from the first
- **Uptime** over 30 days, by time and not by counting checks, so gaps in checking do not distort it
- **Response times:** median and 95th percentile, because an average hides the slow requests people notice

History older than 90 days is removed.

## Responsible use

Only watch systems you run, or that you have permission to check. Frequent automated requests to someone else's service can look like abuse. This tool checks one address at a time on a schedule, and it has no scanning features by design.

## How it is tested

Network checks run against a real local web server and real ports, including a closed port and a missing folder. Outage logic is tested on written-out sequences of passes and failures. App tests use a controllable clock to follow a service down and back up.

## Limits

It has no email or SMS alerts, so someone must look at the page. If this computer loses its own connection, everything looks down, so one watcher cannot tell the difference. There is no ping, since that needs raw sockets. HTTPS certificates are verified, but expiry dates are not reported.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
