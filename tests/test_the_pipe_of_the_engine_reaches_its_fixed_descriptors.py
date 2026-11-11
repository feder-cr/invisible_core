"""The pipe of the engine is on descriptors 3 and 4 in the child, whatever the parent's descriptor table looks like.

`nsRemoteDebuggingPipe.cpp` reads fd 3 and writes fd 4: the numbers are hard-wired in the engine. The launch on POSIX makes them in
the CHILD (`dup2` in `preexec_fn`), so it must not need the PARENT to have anything on 3 and 4. It used to name 3 and 4 in
`pass_fds`, and CPython makes every number of that list inheritable in the child before `preexec_fn` runs: a number that is not
open in the parent fails the whole launch with `[Errno 9] Bad file descriptor`. It does not fail when the pipes the parent made
happen to be on 3 and 4 (the usual case on a bare shell), which is why it was intermittent: under a wrapper that leaves other
descriptors open (`xvfb-run`) the pipes land higher and 3 or 4 may be closed.

The test is a program of its own (a clean descriptor table is the point): a parent with nothing open above 2 and pipes that land far
from 3 and 4 launches a program that reads fd 3 and answers on fd 4, exactly the contract of the engine.
"""
import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.unit,
              pytest.mark.skipif(sys.platform == "win32", reason="POSIX spawn: the engine's pipe is on fds 3 and 4 there")]

PARENT = textwrap.dedent('''
    import fcntl, os, sys
    from invisible_core.juggler import connection

    # The parent's table as a wrapper leaves it: 3 and 4 are closed, and the pipes land far from them.
    for fd in range(3, 64):
        try:
            os.close(fd)
        except OSError:
            pass
    real_pipe = os.pipe

    def far_pipe():
        r, w = real_pipe()
        r2, w2 = fcntl.fcntl(r, fcntl.F_DUPFD, 20), fcntl.fcntl(w, fcntl.F_DUPFD, 20)
        os.close(r)
        os.close(w)
        return r2, w2

    os.pipe = far_pipe
    # the engine: reads fd 3, answers on fd 4, and says what else it holds open
    engine = ("import os; d = os.read(3, 16); "
              "extra = [fd for fd in range(5, 40) if os.path.exists('/proc/self/fd/%d' % fd) or os.path.exists('/dev/fd/%d' % fd)]; "
              "os.write(4, b'pong:' + d + b':' + str(len(extra)).encode())")
    our_write, our_read, process = connection._spawn_posix(sys.executable, ["-c", engine], dict(os.environ))
    os.write(our_write, b"ping")
    sys.stdout.write(os.read(our_read, 64).decode())
    process.wait(timeout=10)
''')


def test_the_launch_does_not_need_descriptors_3_and_4_open_in_the_parent():
    done = subprocess.run([sys.executable, "-c", PARENT], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, "the launch failed: %s" % done.stderr.strip().splitlines()[-1:]
    assert done.stdout.startswith("pong:ping:"), done.stdout


def test_the_engine_keeps_no_descriptor_of_the_parent_but_its_pipe():
    """Nothing else of the parent's table reaches the engine: the two ends of the pipe the parent keeps are closed in the child, and so
    is whatever else the parent had open (the sweep that closed them is gone with `pass_fds`, so the child does it)."""
    done = subprocess.run([sys.executable, "-c", PARENT], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.rsplit(":", 1)[-1] == "0", "the engine holds descriptors above 4 that the parent opened: " + done.stdout
