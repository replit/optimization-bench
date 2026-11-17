import gc
import json
import multiprocessing
import os


def representative():
    gc.set_threshold(901, 11, 12)
    os.environ["EXPECTED_GC_THRESHOLDS"] = json.dumps(gc.get_threshold())
    gc.freeze()
    multiprocessing.set_start_method("spawn", force=True)
    os.environ["REPRESENTATIVE_CALLED"] = multiprocessing.get_start_method()
