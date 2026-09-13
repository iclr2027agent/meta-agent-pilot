# Pattern note

The adversarial prompt caught 8/13 (62%) of the diffs that the neutral prompt
missed 0/13 on in the original genuine cross-model review probe (Section
5.6) — GPT-5 caught 5/6 of Claude's diffs, Claude caught 3/7 of GPT-5's
diffs. This is the "substantially improved" pattern, not the "mostly still
wrong" pattern: reviewer prompting strategy changed the outcome for a
majority of cases in one direction and a minority in the other. One call
(django__django-14140, worker=claude, reviewer=gpt-5) returned an empty
completion on the first attempt and was retried once with a larger token
budget per explicit approval; see anomalies.md.
