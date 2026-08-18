from copy import deepcopy
class TtlCache:
    def __init__(self, ttl, clock):
        if ttl <= 0: raise ValueError("ttl must be positive")
        self.ttl, self.clock, self.items = ttl, clock, {}
    def put(self, key, value): self.items[key] = (deepcopy(value), self.clock() + self.ttl)
    def get(self, key):
        value, deadline = self.items[key]
        if self.clock() >= deadline:
            del self.items[key]
            raise KeyError(key)
        return deepcopy(value)
