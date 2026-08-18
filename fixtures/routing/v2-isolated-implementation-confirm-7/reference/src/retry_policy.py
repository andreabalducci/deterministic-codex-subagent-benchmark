def retry_delay(attempt, base_seconds, cap_seconds):
    if attempt < 1: raise ValueError("attempt is one-based")
    if base_seconds <= 0 or cap_seconds < base_seconds: raise ValueError("invalid bounds")
    return min(cap_seconds, base_seconds * (2 ** (attempt - 1)))
