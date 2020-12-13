import logging
import signal
import time


class Timer:
    def __init__(self, msg=""):
        self.msg = msg

    def __enter__(self):
        self.start = time.time()

        logging.info(f"start {self.msg}")

        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.elapsed_in_milli = (self.end - self.start) * 1000
        self.elapsed_in_milli_as_str = "%0.3f" % self.elapsed_in_milli

        logging.info(f"done {self.msg} in {self.elapsed_in_milli_as_str}ms")


class GracefulSignalHandler(object):
    def __init__(self, sig=signal.SIGINT):
        self.sig = sig

    def __enter__(self):

        self.signaled = False
        self.released = False

        self.original_handler = signal.getsignal(self.sig)

        def handler(signum, frame):
            logging.info(f"received signal {signum}")
            self.signaled = True

        signal.signal(self.sig, handler)

        return self

    def __exit__(self, type, value, tb):
        if self.released:
            return False

        signal.signal(self.sig, self.original_handler)

        self.released = True

        return True
