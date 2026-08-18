using System.Text;
namespace Imports;
public static class CsvRowParser
{
    public static string[] Parse(string row)
    {
        var fields = new List<string>(); var field = new StringBuilder(); var quoted = false;
        for (var i = 0; i < row.Length; i++) { var c = row[i]; if (c == '"') { if (quoted && i + 1 < row.Length && row[i + 1] == '"') { field.Append('"'); i++; } else quoted = !quoted; } else if (c == ',' && !quoted) { fields.Add(field.ToString()); field.Clear(); } else field.Append(c); }
        if (quoted) throw new FormatException("Unclosed quoted field"); fields.Add(field.ToString()); return fields.ToArray();
    }
}
