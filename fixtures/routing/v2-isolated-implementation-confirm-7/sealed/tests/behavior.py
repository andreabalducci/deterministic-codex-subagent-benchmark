import importlib.util
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
module_spec = importlib.util.spec_from_file_location("candidate", path)
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)

assert module.retry_delay(1, 2, 30) == 2
assert module.retry_delay(4, 2, 30) == 16
assert module.retry_delay(8, 2, 30) == 30
for args in ((0, 2, 30), (1, 0, 30), (1, 4, 3)):
    try:
        module.retry_delay(*args)
        raise AssertionError(f"invalid bounds accepted: {args}")
    except ValueError:
        pass
