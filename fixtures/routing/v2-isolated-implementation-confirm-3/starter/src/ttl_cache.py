class TtlCache:
    def __init__(self, ttl, clock): self.ttl, self.clock, self.items = ttl, clock, {}
    def put(self, key, value): self.items[key] = (value, self.clock() + self.ttl)
    def get(self, key):
        value, deadline = self.items[key]
        if self.clock() > deadline: raise KeyError(key)
        return value
