import logging
log = logging.getLogger(__name__)
def trace(request):
    log.info("request headers=%r", request.headers)
