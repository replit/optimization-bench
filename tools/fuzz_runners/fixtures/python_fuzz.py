import sys
import atheris

with atheris.instrument_imports():
    from python_target import classify


def fuzz(data):
    value = int.from_bytes(data[:8], "little", signed=True)
    assert classify(value) == (value > 0) - (value < 0)


atheris.Setup(sys.argv, fuzz)
atheris.Fuzz()
