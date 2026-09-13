# Anomalies

- django__django-14140 [worker=claude reviewer=gpt-5]: first call (max_tokens=8000) returned an empty completion (0 chars, 71.35s wall clock, $0.0808 cost — consistent with the entire token budget being spent on hidden reasoning tokens with none left for visible output). Retried once with max_tokens=16000, identical model/prompt/diff, per explicit user approval (not a prompt variant, not resampling for a better outcome). Retry result: verdict=COMPLETE, now included in the scored totals above.
