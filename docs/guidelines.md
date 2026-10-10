# Guidelines for improving QoS/QoE across network transitions

Deliverable 5 (plan §12). Status: drafted from the mechanism; **rewrite the
numbers from measured results before submission** — each guideline cites
the experiment that backs it.

1. **Trigger preparation on predicted lifetime, not on a signal
   threshold.** A threshold fires when the outage has begun; a forecast
   buys the attach time for free. *(Evidence: P vs P-nopred ablation,
   fig5; P vs B0/B1, fig1.)*

2. **Size the redundancy window to handover latency plus one
   prediction-error margin — no longer.** `T_guard = T_ho − μ + z·σ` is
   derived, not tuned; every extra second is pure duplication cost.
   *(Evidence: model-vs-simulation sweep, fig4.)*

3. **Duplicate only traffic whose interruption is perceptible; never
   duplicate bulk.** Voice and control gaps are audible and unsafe; TCP
   recovers on its own. *(Evidence: P vs P-noclass overhead bars, fig5.)*

4. **Defer bulk during transitions — it funds the duplication.** The
   handover window is a period of scarce usable bandwidth; spend it where
   interruption is perceptible, take it back where it isn't.
   *(Evidence: overhead and bulk-goodput columns in summary.csv.)*

5. **Treat expensive links as partners of last resort, not default
   backups.** Choose the cheapest partner whose predicted lifetime
   outlives the window; satellite only when nothing cheaper qualifies.
   *(Evidence: P vs P-nocost satellite-MB bars, fig5.)*

6. **Enforce a dwell time and a switching margin; together they remove
   most ping-pong.** `T_dwell = 5 s`, utility margin 0.10.
   *(Evidence: ping_pongs column, summary.csv.)*

7. **Validate handover changes with QoE — MOS and stalls — not throughput
   alone.** A transition can preserve throughput while destroying a call.
   *(Evidence: worst-window MOS per mechanism, fig3.)*

8. **Keep the decision stack honest: it may only use what a real device
   could observe at that instant.** Enforce it structurally (the trace is
   visible to the replayer only) and state it. *(Plan §9.6.)*

9. **In contested environments, pin the expensive link warm.** Prediction
   cannot see an abrupt kill coming (jamming has no radio precursor); a
   pre-attached satellite turns a ~2.8 s outage into ~0.3 s at roughly
   double the satellite bill. Make that a mode switch, not a redesign.
   *(Evidence: stress_summary.csv — P vs P-nocost under jamming.)*

10. **Forecast error fails safe when the response is a window, not a
    switch.** Degrading the predictor converts into extra duplication
    seconds, not into outage — so deploying an imperfect model is a cost
    decision, not a reliability risk. *(Evidence: fig6 robustness sweep.)*
