from datetime import datetime
class Schedule:
    def __init__(self, run_at, tags=[]):
        self.run_at = run_at
        self.tags = tags
