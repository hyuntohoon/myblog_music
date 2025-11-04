import threading

class SingleFlight:
    def __init__(self):
        self._locks = {}
        self._g = threading.Lock()

    def acquire(self, key: str):
        with self._g:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
        lock.acquire()
        return key

    def release(self, key: str):
        with self._g:
            lock = self._locks.get(key)
        if lock:
            lock.release()

single_flight = SingleFlight()
