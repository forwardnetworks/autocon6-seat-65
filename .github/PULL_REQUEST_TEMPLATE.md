## Change request CHG-AC6-0001: retire the stale BGP advertisements on r4

**What this change does:** <!-- e.g. retires 10.20.30.0/24 and 10.20.40.0/24 on r4 -->

**What must still work:** the existing application (TCP 8080), the blocked management port (TCP 2222),
R1's route to the service via R2, and the primary path R1 → R2 → R4.

- [ ] Only `candidate/r4-bgp.eos` is changed
- [ ] I read the **forward/predict** check before asking for approval
