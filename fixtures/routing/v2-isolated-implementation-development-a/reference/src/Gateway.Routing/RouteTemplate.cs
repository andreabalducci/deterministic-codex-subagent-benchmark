namespace Gateway.Routing;
public static class RouteTemplate
{
    public static string Canonicalize(string value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(value);
        var segments = value.Trim().Split('/', StringSplitOptions.RemoveEmptyEntries);
        return "/" + string.Join('/', segments.Select(s => s.StartsWith('{') && s.EndsWith('}') ? s : s.ToLowerInvariant()));
    }
}
