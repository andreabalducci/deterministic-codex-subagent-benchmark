namespace Auth; public static class ScopeMatcher { public static bool HasScope(string? claim, string required) => claim?.Contains(required, StringComparison.Ordinal) == true; }
