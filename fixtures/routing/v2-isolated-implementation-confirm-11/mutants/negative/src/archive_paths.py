from pathlib import PurePosixPath
def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts: raise ValueError('unsafe archive member')
    return path
