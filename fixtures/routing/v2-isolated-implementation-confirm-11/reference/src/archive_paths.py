from pathlib import PurePosixPath
def safe_member(name):
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts): raise ValueError('unsafe archive member')
    if ':' in path.parts[0]: raise ValueError('drive-qualified member')
    return path
