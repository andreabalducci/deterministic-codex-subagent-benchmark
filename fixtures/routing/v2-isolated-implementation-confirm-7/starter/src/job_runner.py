from retry_policy import retry_delay
def should_retry(status): return status in {408, 429, 503}
