namespace Gateway.Routing;
public static class RouteTemplate
{
    public static string Canonicalize(string value)
        => value.Trim().ToLowerInvariant().TrimEnd('/');
}
