# Verifier-only NumPy differential harness

The shared executor uses a fresh container per case, seeds 1729/2718/31415,
twelve cases per seed, deterministic shrinking and mismatch reproducers.
Real array fragments cover dtype/shape/axis/q/method/NaN and view combinations,
invalid arguments, independent/interleaved calls and input preservation.
Float results reuse the unchanged protected dtype/shape/8eps comparator; integer
results and error/warning transcripts are exact. Current receipts establish passage.
