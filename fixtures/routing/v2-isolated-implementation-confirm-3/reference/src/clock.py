class ManualClock:
    def __init__(self, now=0.0): self.now = now
    def __call__(self): return self.now
