import numpy as np
import hashlib

def array_hash(array):
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()

def prepare(parent, operations):
    array = parent
    for operation in operations:
        kind = operation["op"]
        if kind == "cast":
            array = array.astype(operation["dtype"], order="K", copy=True)
        elif kind == "slice":
            index = tuple(slice(*item) if isinstance(item, list) else item
                          for item in operation["index"])
            array = array[index]
        elif kind == "transpose":
            array = array.transpose(operation["axes"])
        elif kind == "contiguous":
            array = np.array(array, order=operation["order"], copy=True)
        else:
            raise ValueError(f"Unknown operation: {kind}")
    return array

def arguments(case):
    args = case["call"]
    axis = args["axis"]
    if isinstance(axis, list):
        axis = tuple(axis)
    return {"q": np.asarray(args["q"], dtype=np.float64), "axis": axis,
            "method": "linear", "keepdims": args["keepdims"],
            "overwrite_input": False}

def buffer_hash(array):
    owner = array
    while isinstance(owner.base, np.ndarray):
        owner = owner.base
    return array_hash(owner)
