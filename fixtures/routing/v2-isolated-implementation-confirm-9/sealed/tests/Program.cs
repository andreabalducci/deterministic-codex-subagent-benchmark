using Auth;
Check(ScopeMatcher.HasScope("read write", "write"), "exact");
Check(!ScopeMatcher.HasScope("orders.read", "orders"), "prefix");
Check(ScopeMatcher.HasScope("read   write", "write"), "spacing");
static void Check(bool ok, string name) { if (!ok) throw new Exception(name); }
