namespace Imports;
public static class CsvRowParser { public static string[] Parse(string row) => row.Split(',', StringSplitOptions.RemoveEmptyEntries); }
