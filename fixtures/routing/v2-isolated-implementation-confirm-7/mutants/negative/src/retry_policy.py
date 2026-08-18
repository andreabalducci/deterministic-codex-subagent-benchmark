def retry_delay(attempt, base_seconds, cap_seconds):
    if attempt < 1: raise ValueError("attempt is one-based")
    return min(cap_seconds, base_seconds * (2 ** attempt))
