"""Small process-local cache for expensive aggregate metadata reads only."""
from collections import OrderedDict
from threading import Lock
from time import monotonic

class ReadCache:
    def __init__(self, capacity=64, ttl=10):
        self.capacity, self.ttl = capacity, ttl
        self.values = OrderedDict()
        self.lock = Lock()

    def get(self, key, loader):
        with self.lock:
            saved = self.values.get(key)
            if saved and saved[0] > monotonic():
                self.values.move_to_end(key)
                return saved[1]
            value = loader()  # runs in caller; no shared DB session across threads
            self.values[key] = (monotonic()+self.ttl, value)
            self.values.move_to_end(key)
            while len(self.values) > self.capacity:
                self.values.popitem(last=False)
            return value

gallery_counts = ReadCache()
