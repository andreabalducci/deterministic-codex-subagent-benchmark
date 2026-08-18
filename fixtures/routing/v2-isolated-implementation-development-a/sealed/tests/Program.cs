using Gateway.Routing;
Check(RouteTemplate.Canonicalize("//Orders///{OrderId}/") == "/orders/{OrderId}", "segments and token case");
Check(RouteTemplate.Canonicalize("/") == "/", "root");
try { RouteTemplate.Canonicalize("   "); throw new Exception("blank accepted"); } catch (ArgumentException) {}
static void Check(bool ok, string name) { if (!ok) throw new Exception(name); }
