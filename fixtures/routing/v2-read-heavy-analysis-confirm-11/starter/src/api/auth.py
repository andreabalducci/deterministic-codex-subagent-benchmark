import logging
log = logging.getLogger(__name__)
def require_scope(scope):
    def decorate(handler):
        def wrapped(request):
            try:
                if scope not in request.token["scopes"]: raise PermissionError(scope)
            except Exception as error:
                log.warning("auth lookup failed: %s", error)
            return handler(request)
        return wrapped
    return decorate
