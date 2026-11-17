"""Join already-created CPython services before the measured CLI exits."""
import atexit
import os
import sys

_registered_pid = os.getpid()


def _shutdown():
    # A fork inherits atexit callbacks without transferring the parent's services.
    if os.getpid() != _registered_pid:
        return
    # Forkserver holds tracker FDs; these APIs stop only this interpreter's services.
    forkserver = sys.modules.get("multiprocessing.forkserver")
    if forkserver is not None and forkserver._forkserver._forkserver_pid is not None:
        server = forkserver._forkserver
        try:
            server._stop()
        except FileNotFoundError as error:
            # util may remove the socket's tempdir before this already-joined server's unlink.
            if (server._forkserver_pid is not None or error.filename is None
                    or error.filename != server._forkserver_address):
                raise
    tracker = sys.modules.get("multiprocessing.resource_tracker")
    if tracker is not None and tracker._resource_tracker._pid is not None:
        tracker._resource_tracker._stop()


# LIFO: application/multiprocessing finalizers registered later must finish first.
atexit.register(_shutdown)
