namespace Gateway.Routing;
public static class RouteTemplate
{
    public static string Canonicalize(string value)
    {
        var segments = value.Trim().Split('/', StringSplitOptions.RemoveEmptyEntries);
        return "/" + string.Join('/', segments.Select(s => s.ToLowerInvariant()));
    }
}
