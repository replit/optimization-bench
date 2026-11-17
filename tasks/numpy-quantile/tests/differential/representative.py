"""Ordinary public calls for the verifier's separate post-call state observer."""
def run(np):
    a = np.arange(105, dtype="float64").reshape(3, 5, 7)
    for axis in (None, 0, -1):
        np.quantile(a, [.1, .5, .9], axis=axis, keepdims=True, overwrite_input=False)
