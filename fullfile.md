EDGE Challenge — Solution v2: Predictive, Class-Aware Selective Redundancy
ATP 2026 · EDGE Group track · merged team plan Version: v2 draft, 23 Sep 2026 · Submission: 11 Oct 2026, 23:59 GMT+4

This document replaces solution-1.md, the APSH plan and the earlier handoff as the single plan the team works from. Every item carries a status: Locked (don't reopen without a concrete reason), Proposed (default we build on unless someone objects this week), or Open (needs an owner and a decision).

0. Where this came from
Each input contributed something; nothing here is invented from scratch.

Source	What v2 keeps from it
Solution 1 + handoff	The mechanism (predicted-window, class-aware, cost-aware duplication), scenario, baselines B0–B2, observability rule, three implementation layers, monitor design
APSH plan	Real traces (Raca 4G/5G datasets), named references, data models, utility function, per-technology attach times, ablations, speed × direction × seed grid, simulator design, repo layout
Claude HTML version of Solution 1	"Bulk funds duplication" framing, the two-extremes and timing diagrams for the deck, labelling expected figures as hypotheses
ChatGPT review	Predictor output as a forecast of future link state rather than an abstract score; drop wired as a real link; no custom kernel scheduler
Manus survey	Tooling catalogue; MPTCP limitations; reject real VPS in favour of namespaces for determinism
Official brief + FAQ	10-page PDF, video must cover all five content items, both speeds and directions, AI use must be declared
1. The brief in one screen
Problem: keep users' experience unbroken while moving between Wi-Fi, mobile, satellite and wired networks, with different applications, speeds and directions.
Success criteria: analyse 1–2 existing mechanisms properly; propose an adapted mechanism; prototype via simulation/emulation; compare against existing mechanisms; write QoS/QoE guidelines.
Deliverables: a 10-page PDF and a 1–2 minute video, together covering problem, solution, system diagram, demo, and real-life deployment plan. Prototype and simulation and model is explicitly stronger.
Rubric: originality 25% · implementation 20% · method 20% · results vs existing 20% · clarity 15%.
AI use: allowed, must be declared, and every member must be able to explain the solution in their own words. Each owner must be able to defend their component at sponsor Q&A.
After 11 Oct: shortlist by 25 Oct, then ~1.5 weeks mentoring, then NSTI demo days in November. The live dashboard and extensions can wait until then.
2. Scenario
Status: Locked

A mission-critical vehicle (emergency response / defence logistics) drives depot → urban → highway → rural fringe → remote site, and the same route in reverse.

Leg	Environment	Links	Dominant transition
A	Depot / urban	Wired (parked only), Wi-Fi, 5G	Wired/Wi-Fi → 5G on departure
B	Highway 80–120 km/h	5G, occasional Wi-Fi	5G coverage dips
C	Rural fringe	Weak 5G, LEO satellite	5G collapse → satellite
D	Remote site	LEO satellite only	Obstruction events
Wired is only the depot starting state, not an experimental link.
Satellite is LEO (Starlink-like: low RTT, periodic reconfiguration, obstruction outages), not GEO. LEO makes the cost-vs-latency choice genuinely hard; GEO makes it trivial.
Directions: both. The reverse run is the forward trace replayed backwards.
Speeds: emulation uses the speeds recorded in the traces; the simulator sweeps 30 / 60 / 90 / 120 km/h.
Traffic classes (vehicle → operations, uplink):

Class	Flows	What breaks	Response in the predicted window
Critical	VoIP command channel (G.711-like, 50 pkt/s), telemetry (10 pkt/s)	Any gap is perceptible; late data is useless	Duplicate on two links
Interactive	Live video feed (~2–4 Mbps)	Small buffer; stalls if the gap is longer than it	Pre-switch to the new link
Bulk	Map/data sync, log upload (TCP)	Nothing visible; TCP recovers	Defer / throttle
The unifying idea: a handover window is a period of scarce usable bandwidth. We spend it where interruption is perceptible and take it back where it isn't. Bulk funds duplication.

Downlink is symmetric in principle; we measure uplink only and say so.

3. Existing mechanisms and references
Status: Proposed (citations must be verified before they go in the deck — AI-drafted plans do invent references)

Analyse two properly, cite the rest briefly.

Primary reference 1 — predictive handover with MPTCP. Learning Wi-Fi Connection Loss Predictions for Seamless Vertical Handovers Using Multipath TCP, arXiv:1907.10493 (2019). Predicts Wi-Fi loss, then brings MPTCP into play. This is the closest prior art to us and the reference we position against: it predicts whether to prepare a second path; it does not decide which traffic gets protected, and it is not cost-aware when an expensive link is the only partner.

Primary reference 2 — 3GPP ATSSS (TS 23.501, Rel-16+). The standardised way to steer, switch and split traffic between 3GPP and non-3GPP access. Steering modes are policy-driven and largely static; no predicted redundancy window.

Supporting: RFC 8684 (MPTCP) and the redundant-scheduler idea, for baseline B2; Ahmed, Boulahia & Gaiti, IEEE Comms Surveys & Tutorials 2014, to frame vertical-handover decision algorithms; Raca et al. MMSys 2018/2020 for the datasets.

The gap, stated once: existing mechanisms either never duplicate (outages at every transition) or always duplicate (bandwidth and satellite cost for all traffic). Predictive approaches like reference 1 fix when to prepare, but still treat all traffic the same. We duplicate only critical flows, only inside the predicted window, with the cheapest partner that will survive it.

4. The mechanism
Status: Locked (core) / Proposed (details)

Three ideas combined:

Predict, don't react. Forecast each link's remaining usable lifetime from short-window trends, so action starts while the current link still works.
Protect selectively. Inside the predicted window only: duplicate critical flows, pre-switch interactive flows, defer bulk.
Respect cost. Satellite is a redundancy partner of last resort: used only when no cheaper link is predicted to outlive the window.
What is genuinely ours: the predicted-window redundancy trigger, per-class treatment inside that window, and cost-aware partner selection. This is an adaptation of known pieces, which the brief explicitly permits.

5. Architecture
Status: Locked

flowchart LR
    APPS["Apps: voice, telemetry, video, bulk"] --> TUN["TUN interface<br/>(gateway namespace)"]
    TUN --> CLS["3 · Classifier"]
    CLS --> DP["5 · Tunnel data plane<br/>steer / duplicate / throttle"]
    MON["1 · Link monitor<br/>10 Hz"] --> PRED["2 · Predictor<br/>T_life per link"]
    PRED --> POL["4 · Policy engine"]
    CLS --> POL
    POL --> DP
    DP --> W["Wi-Fi veth + netem"]
    DP --> G["5G veth + netem"]
    DP --> S["LEO veth + netem"]
    W --> PX["6 · Aggregation proxy<br/>dedup + receive log"]
    G --> PX
    S --> PX
    PX --> SRV["Servers (unchanged)"]
    MON -. probes .-> W
    MON -. probes .-> G
    MON -. probes .-> S
#	Component	Responsibility	Output
1	Link monitor	Sample each link at 10 Hz: replayed radio metrics + self-measured RTT, loss, delivery rate	Monitor records (JSON, logged)
2	Predictor	Estimate each link's remaining usable lifetime	T_life_hat, p_fail(H)
3	Classifier	Tag flows critical / interactive / bulk by port/protocol	Flow class
4	Policy engine	Decide paths and rate cap per flow — the core contribution	Flow decisions
5	Tunnel data plane	Encapsulate, send each packet on its assigned link(s), throttle bulk	Packets on links
6	Aggregation proxy	De-duplicate, reorder, forward, log every received packet	Clean stream; ground-truth log
Components 1–5 run on the vehicle gateway; 6 runs at the edge. The proxy is not optional: duplication without a de-duplicating receiver doesn't work.

5.1 Why a userspace tunnel, not MPTCP
Status: Locked

The flows we protect (voice, telemetry, live video) are UDP. MPTCP is TCP-only, so it can never carry them.
Upstream Linux MPTCP doesn't give us a per-flow, per-packet duplicating scheduler without kernel/BPF work — a scope trap.
In a userspace tunnel, steer / duplicate / throttle are a few dozen lines, and all baselines run on the same code path, which makes the comparison fair.
Kernel MPTCP still appears: it runs on the bulk TCP flow as an external check that our B1 reimplementation behaves like the real thing.
5.2 Data models
Status: Proposed — freeze by 26 Sep, because these are the interfaces between the four owners.

@dataclass
class LinkState:            # monitor output, one per link per 100 ms
    t: float
    link: str               # "wifi" | "5g" | "leo" | "wired"
    signal: float           # normalised 0..1 (RSSI / RSRP / SNR mapped per tech)
    rtt_ms: float
    jitter_ms: float
    loss: float             # 0..1, sliding window
    delivery_mbps: float
    signal_slope: float
    rtt_slope: float
    speed_kmh: float
    heading: float
    attached: bool
    cost_per_mb: float      # static per tech

@dataclass
class Prediction:
    t: float
    link: str
    t_life_hat: float       # seconds until unusable, capped at 10 s
    p_fail_h: float         # P(unusable within H = 3 s)

@dataclass
class ClassProfile:          # one row per traffic class
    name: str               # "critical" | "interactive" | "bulk"
    max_rtt_ms: float
    max_loss: float
    min_mbps: float
    w_latency: float; w_loss: float; w_bandwidth: float; w_cost: float

@dataclass
class FlowDecision:
    t: float
    flow_id: int
    cls: str
    paths: list[str]        # one link = steer, two links = duplicate
    rate_cap_mbps: float | None   # set for deferred bulk
Monitor records keep the JSON format already agreed in the handoff, extended with attached and signal.

6. Predictor
Status: Proposed

Target: T_life = time until the link becomes unusable. Proposed definition of unusable: loss ≥ 5% over a 500 ms window, or RTT ≥ 300 ms, or link down — sustained for ≥ 300 ms. These are the critical-class limits, which is what makes the trigger meaningful for the flows we duplicate. Labels are computed offline from emulator ground truth; features only from the monitor (observability rule).
Output: T_life_hat (regression, capped at 10 s) and p_fail(H=3 s) derived from it.
Features: current signal, slope and variance of signal and RTT over 1 s and 3 s windows, loss, delivery rate, link age, time since last handover, speed.
Models: linear slope extrapolation (explainable baseline) and a gradient-boosted regressor (sklearn HistGradientBoostingRegressor). Report both; ship whichever wins on held-out traces. No LSTM.
Training data: Raca 4G/5G traces, train/eval split by trace, never within a trace. Stated in the deck.
Accuracy to report: mean and σ of the error T_life − T_life_hat on held-out traces. σ feeds the analytical model (§9.3).
Known limitation: trained on cellular data; applied to Wi-Fi and LEO through normalised features. State it.
6.1 Map prior (Option A)
Status: Proposed, gated

A per-road-segment coverage prior learned from the monitor logs of training traversals only (EWMA mean/variance, observation count, direction bucket), fused with the live predictor by confidence. Unmapped segments fall back to exactly the live predictor.

Why it earns its place: satellite attach takes longer than a live slope predictor can see ahead, so the 5G → LEO transition is where Solution 1 alone is weakest. The map gives warning in minutes.

Gate: only built if P is producing emulation results against B0 and B1 by 2 Oct. Otherwise it becomes a future-work slide. It must never be a dependency of the core.

7. Policy engine
Status: Proposed

every 100 ms:
    A = primary link
    pred[L] = predictor(L) for every link L

    # 1. start a protected transition
    if not in_transition and pred[A].t_life_hat < T_guard and time_since_last > T_dwell:
        P = choose_partner(A)
        attach(P)                                  # pays t_attach[P]
        for flow F:
            CRITICAL    -> paths = {A, P}          # duplicate
            INTERACTIVE -> paths = {P} once P passes probe, else {A}
            BULK        -> rate_cap = r_defer      # defer

    # 2. complete it
    if in_transition and P probed OK and stable for T_hold
       and (A unusable or utility(P) - utility(A) > margin):
        primary = P; critical -> {P}; bulk cap removed; release A

    # 3. cancel a false alarm
    if in_transition and pred[A].t_life_hat > T_guard + hysteresis for T_hold:
        critical -> {A}; bulk cap removed; release P

    # 4. hard fallback
    if A goes down with no partner ready:
        break-before-make to best available link, gap recorded

choose_partner(A):
    candidates = attached-or-attachable links != A
                 where feasible(L, CRITICAL) and pred[L].t_life_hat > T_guard + T_hold
    prefer lowest cost_per_mb among candidates with utility within margin of the best
    satellite only if no cheaper candidate qualifies
utility(L, class) is APSH's weighted score: latency, loss and bandwidth terms against the class profile minus a cost term; infeasible links score −∞.

Constants (Proposed defaults, all reported in the deck): T_guard from the model (§9.3), T_hold = 1 s, T_dwell = 5 s, margin = 0.10, H = 3 s, r_defer = 10% of bulk's normal rate.

8. Data plane and testbed
Status: Proposed (owner decides implementation details)

Namespaces: gw (apps, TUN, tunnel client, monitor, predictor, policy), wifi/5g/leo veth pairs with netem, proxy, srv.

Tunnel:

Header per packet: flow_id (u16), seq (u32), t_send_ms (u64) — 14 bytes.
One UDP socket per link, bound to that link's veth.
Proxy de-duplicates by (flow_id, seq) with a sliding bitmap (4096), forwards the first copy, and logs every arrival with link and timestamp. That log is the ground truth for all metrics.
Apps enter through a TUN device so real TCP (bulk, iperf3) works unchanged. Fallback if TUN is painful: traffic generators talk to the tunnel on local UDP ports; bulk becomes a rate-controlled UDP stream.
Python first. Rates are modest (voice 50 pkt/s, video ~400 pkt/s, bulk a few thousand pkt/s). Move to Go only if CPU becomes the bottleneck — measure first.
Trace replay: a replayer updates each link's netem rate/delay/loss every 100 ms (pyroute2 or tc qdisc change). A detached link is 100% loss; attach(L) makes it usable only after t_attach[L]. The trace is visible to the replayer only, never to components 1–4.

Attach times (placeholders — cite before use): Wi-Fi 300 ms, 5G 150 ms, LEO TBD from published Starlink measurements, probe 100 ms. Baselines that react to failure also pay a detection delay (t_detect, e.g. 1 s).

Run length: real time. A full route is too long, so each run replays a compressed route of ~3 minutes containing every transition. Independent runs go in parallel namespace sets. Budget: ~7 configurations × 2 directions × 20 repetitions × 3 min ≈ 14 h sequential, ~3.5 h with 4 in parallel.

9. Evaluation
9.1 Baselines and ablations
Status: Locked (B0–B2) / Proposed (ablations)

All run on the same tunnel and the same traces.

ID	Mechanism	Represents
B0	Single link, signal threshold + hysteresis, break-before-make	What a normal device does today
B1	minRTT multipath: all traffic on lowest-RTT link, failover on detected loss	MPTCP default behaviour — cross-checked against kernel MPTCP on the bulk TCP flow
B2	Always duplicate every flow on the two best links	Upper bound on continuity, and its cost
P	Ours	The proposal
Ablation	Removes	Proves
P − prediction	Window triggered by threshold crossing instead of forecast	Prediction is where the lead time comes from
P − classes	All flows duplicated in the window	Selectivity is where the cost saving comes from
P − cost	Partner chosen by quality only	Cost-awareness keeps satellite bytes down
P + map	Adds the map prior (if built)	Earlier warning on 5G → LEO
Optional, only if time allows: ATSSS-like static priority steering (APSH's B2). The 2024 LSTM+DQL paper is compared qualitatively, not reproduced.

9.2 Metrics
Metric	Definition	Applies to
Interruption time	Longest gap in received packets across a transition	All
Loss during handover	Loss within ±2 s of a transition	All
Voice MOS	ITU-T G.107 E-model (simplified, G.711: Ie = 0, Bpl = 25) from delay, loss	Voice
Telemetry gap	Longest period with no telemetry delivered	Telemetry
Video stalls	Count and total duration, receiver buffer model	Video
Latency / jitter	p50 and p95 per flow	All
Bandwidth overhead	Duplicate bytes ÷ useful bytes	P, B2
Satellite bytes	Cost proxy	All
Handovers / ping-pongs	Count; ping-pong = return within T_dwell	All
Headline numbers: interruption time and voice MOS.

9.3 Analytical model (draft for Q1)
Status: Open — owner validates and finishes

At the trigger, the predictor says ≈ T_guard seconds remain. True remaining life is T_guard + ε, where ε is prediction error with mean μ and s.d. σ measured on held-out traces (assume normal first; check the fit). The handover needs T_ho = t_attach + t_probe. The interruption is whatever the lead time falls short by:

I = max(0, T_ho − T_guard − ε)

a = T_ho − T_guard − μ
E[I] = a·Φ(a/σ) + σ·φ(a/σ)            (expected shortfall of a normal)
P(I > 0) = Φ(a/σ)
Expected duplication time per transition ≈ T_guard + μ + T_hold
Choosing a target miss probability Φ(−z) gives T_guard = T_ho − μ + z·σ — the handover latency plus a prediction-error margin, derived rather than tuned. Missed detections (predictor never fires) add p_miss · (t_detect + T_ho) to E[I].

Validation: plot model E[I] against emulated interruption as T_guard is swept. Agreement is a reportable result.

9.4 Simulation (parameter sweep)
Status: Proposed

APSH's Python simulator, adopted as the sweep layer: time-stepped, seeded, same policy code and profiles as the emulation. It sweeps what emulation can't afford: speed (30–120 km/h), predictor error σ (deliberately degraded), T_guard, satellite cost ratio, 30 seeds each. Key output: how bad can the predictor get before P stops beating B1?

Its link models (log-distance path loss for Wi-Fi, Raca-driven 5G, LEO per §2) are stated as simplifications. Its headline results never replace the emulation's.

9.5 Figures for the deck
Timeline of one run: link quality per link, active link per class, duplication on/off, packet gaps for B0 vs P. The "aha" figure.
Interruption vs overhead, B0/B1/B2 as points and P as a curve swept over T_guard. Labelled as a hypothesis until measured.
Voice MOS and interruption per mechanism, mean ± 95% CI, both directions.
Ablation bars.
Model vs emulation, E[I] against T_guard.
Robustness sweep: interruption vs predictor error σ.
(If built) Map learning curve: interruption vs number of prior traversals.
9.6 Rigour rules
Status: Locked

Observability rule: decision stack sees only what a real device could see; enforced by exposing traces to the replayer only. Stated in the deck.
Predictor train/eval disjoint by trace.
Fixed, recorded seeds; 20 repetitions per emulation configuration, 30 per simulation configuration; mean ± 95% CI everywhere.
Plots generated from logs by script only.
One command reproduces everything (pinned script or Docker).
9.7 Stress scenario
Status: Proposed

One jamming / deliberate-degradation trace on 5G (abrupt, non-gradual loss). Reported as robustness: we expect prediction to help less, and B2-like behaviour to be the fallback. Honest reporting of a weaker case strengthens the method score.

10. Demo and video
Status: Proposed

For 11 Oct: a scripted side-by-side replay from logged runs — B0 and P on the same trace: active link per class, T_life countdown, duplication indicator, voice gap and MOS. Real audio through the voice path is a bonus, not required.

Video (~1:45):

Time	Content	Brief item
0:00–0:15	The problem: a command call dropping as the vehicle leaves 5G coverage	Problem
0:15–0:40	The idea + architecture diagram	Solution, diagram
0:40–1:25	Side-by-side replay: B0 breaks, P doesn't; overhead counter	Demo
1:25–1:45	Deployment: in-vehicle gateway, edge proxy, fleet rollout	Deployment
After shortlisting: live dashboard for the NSTI stand; application-negotiated handover (Option E) is the natural demo upgrade.

11. Deployment plan
Status: Proposed

In-vehicle multi-link gateway (SD-WAN-style box with Wi-Fi, 5G modem and LEO terminal) or an agent on an end device; aggregation proxy at a regional edge site. Only the monitor's drivers change from emulation to hardware (iw, ModemManager, the dish API, gpsd). Maps onto ATSSS steering modes; could ride MPQUIC once standardised. Rollout: small fleet pilot, central cost and policy tables, predictor retrained per region from collected monitor logs.

12. Guidelines (deliverable 5)
Status: Proposed — rewrite from measured results before submission

Trigger preparation on predicted lifetime, not on a signal threshold.
Size the redundancy window to handover latency plus one prediction-error margin — no longer.
Duplicate only traffic whose interruption is perceptible; never duplicate bulk.
Defer bulk during transitions; it funds the duplication.
Treat expensive links as partners of last resort, not default backups.
Enforce a dwell time and a switching margin; together they remove most ping-pong.
Validate handover changes with QoE (MOS, stalls), not throughput alone.
13. Team and ownership
Status: Open — assign names this week, ideally by who wrote which draft.

Owner	Scope	First deliverable
Person 1 — data plane	Namespaces, TUN, tunnel, proxy, trace replayer, attach emulation, B0/B1/B2 modes, kernel MPTCP cross-check	Voice flow crossing two emulated links with dedup
Person 2 — traces and prediction	Raca download and conversion, LEO and Wi-Fi trace models, monitor, predictor, map prior	Converted traces + held-out split
Person 3 — decision and theory	Policy engine, class profiles, analytical model, simulator	Policy running against logged monitor records
Person 4 — evidence	Traffic generators, metrics (E-model, stalls, gaps), experiment runner, figures, deck, video	Metrics computed from a proxy receive log
Persons 2–4 can start immediately against recorded data; nobody waits for the testbed.

14. Timeline
Status: Proposed

By	Milestone
26 Sep	Data models and log formats frozen; Raca traces converted; tunnel carries voice over two links
29 Sep	Wi-Fi ↔ 5G testbed running; B0 and B1 producing results; predictor v0 trained; simulator v0 runs
2 Oct	P integrated end-to-end; LEO link added; map-prior go/no-go
5 Oct	Full emulation grid + ablations done; simulation sweep done; model validated
8 Oct	Figures frozen; deck and video drafts complete
10 Oct	Final review and submit — one day of buffer before the 11 Oct deadline
15. Cut order
Status: Proposed

Cut in this order if behind:

Map prior (future-work slide)
Optional baselines (ATSSS-like static steering)
Jamming stress scenario
Simulation breadth (fewer sweep dimensions)
Real audio in the demo (packet-gap visual only)
Never cut: tunnel + proxy, predictor, policy engine, B0, B1, B2, the "P − prediction" ablation, and the analytical model in at least its one-equation form.

16. Open questions
#	Question	Owner
Q1	Finish and validate the analytical model (§9.3)	Person 3
Q3	Linear vs gradient-boosted predictor — decide on held-out results	Person 2
Q4	Cite a source for every link parameter, especially LEO attach time and reconfiguration behaviour	Person 2
Q6	Exact classification rules (ports/protocols) per class	Person 1
Q7	Confirm T_hold = 1 s from emulation	Person 3
Q8	Names against the four roles	Team
Q10	Confirm the "unusable" definition in §6 against E-model sensitivity	Persons 2 + 4
Q11	Verify every reference in §3 exists and says what we claim	Person 4
17. Deck outline (10 pages)
Each page makes one claim tied to a rubric criterion, backed by one figure.

Problem and why it matters — the dropped command call
Existing mechanisms — the two-extremes diagram; reference 1 and ATSSS
Our mechanism — predict, protect selectively, respect cost; timing diagram
Architecture — component diagram and data path
Prediction — features, model, accuracy on held-out traces
Method — testbed, traces, observability rule, baselines, seeds and CIs
Headline results — interruption vs overhead; MOS per mechanism
Why it works — ablations; model vs emulation; robustness sweep
Deployment plan and limitations
Guidelines + AI-use declaration
18. AI use declaration (draft)
We used AI assistants (Claude, ChatGPT, Manus) for literature search, design review and drafting. All design decisions, code, experiments and results were produced, checked and are explained by the team.

Edit to match what's actually true by submission.
