using Imports;
Check(CsvRowParser.Parse("a,\"b,c\",d").SequenceEqual(new[]{"a","b,c","d"}), "quoted comma");
Check(CsvRowParser.Parse("\"a\"\"b\"").Single() == "a\"b", "escaped quote");
Check(CsvRowParser.Parse("a,b,").Length == 3, "trailing empty");
try { CsvRowParser.Parse("\"open"); throw new Exception("unclosed accepted"); } catch (FormatException) {}
static void Check(bool ok, string name) { if (!ok) throw new Exception(name); }
