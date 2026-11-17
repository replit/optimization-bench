from hypothesis import given, seed, settings, strategies as st
from python_target import classify


@seed(1729)
@settings(max_examples=100, database=None, deadline=None)
@given(st.integers())
def test_sign(value):
    assert classify(value) == (value > 0) - (value < 0)


if __name__ == "__main__":
    test_sign()
