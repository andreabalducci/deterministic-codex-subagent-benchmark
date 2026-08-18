namespace Auth;
public static class ScopeMatcher
{
    public static bool HasScope(string? claim, string required)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(required);
        return claim?.Split(' ', StringSplitOptions.RemoveEmptyEntries).Contains(required, StringComparer.Ordinal) == true;
    }
}
