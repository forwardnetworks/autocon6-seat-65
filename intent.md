# Intent

## Intent

Retire r4's stale BGP advertisements: remove every `network` statement on r4 (AS 65004) whose prefix r4 has no
route for, and keep every advertisement that carries live traffic. The client at 10.10.10.10 must still reach
the service at 10.20.20.20 on TCP 8080, and TCP 2222 must stay blocked.

## How this intent becomes tests

The intent above is written for people and for Forward AI. The machine-checkable version is
`requirements/candidate.yml`: Predict evaluates every candidate change against it before anything touches a
router, and `workshop verify` checks the same requirements again on the real network afterwards.

Forward AI can analyse the network, draft a change and explain a failed prediction. It never decides whether a
change is safe: only the requirements, evaluated on the predicted network, do that, and only a person approves
and deploys.
