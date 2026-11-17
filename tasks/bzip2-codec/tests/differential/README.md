# Verifier-only bzip2 differential harness

The shared executor uses a fresh container per case, seeds 1729/2718/31415,
twelve cases per seed, deterministic shrinking and mismatch reproducers.
Real input fragments cover levels, streaming chunks, malformed streams and
independent sessions. Buffer capacities sweep below/at/+1 through +8 around
the pristine encoded size, with destination canaries and complete transcripts.
Current qualification receipts, not this coverage description, establish passage.
