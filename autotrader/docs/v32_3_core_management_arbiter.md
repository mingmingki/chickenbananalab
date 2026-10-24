# CORE v32-3: atomic/discretionary decision scheduling
- Emergency stop guard runs first as v32-2; exchange OCO unchanged.
- Recovery of pending REDUCE/ADD, including OCO verify, runs next as v32-2.
- On each fresh 30s fast tick, MFE-profit reduction comes before a *single* profit-floor amendment; reducer no longer attempts a duplicate floor internally.
- If confirmed stop amend was executed, prior reduction was executed, or early Gemini profit review was started, this tick ends. Never start another negative-guard/Gemini reduce in the same tick.
- If reduce reply is ambiguous or pending recovery state cannot be proved READY, defer every additional discretionary action; maintain existing exchange protection.
- If no effective action happened, original negative guard and loss AI can still run.
- No modification to hard stop, SL/TP distance, position size, entry parameters or Candidate C actions.
- This is not exchange-level transactional atomicity; optimize repeated operations only, not financial expectancy.
