from archive_paths import safe_member
def extract_member(root, member):
    relative = safe_member(member)
    return root / relative
