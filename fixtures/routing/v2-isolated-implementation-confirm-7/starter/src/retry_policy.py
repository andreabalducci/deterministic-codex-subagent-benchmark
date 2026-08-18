def retry_delay(attempt, base_seconds, cap_seconds):
    return min(cap_seconds, base_seconds * (2 ** attempt))
