using System.Text;
namespace Imports;
public static class CsvRowParser
{
    public static string[] Parse(string row)
    {
        var fields = new List<string>(); var field = new StringBuilder(); var quoted = false;
        foreach (var c in row) { if (c == '"') quoted = !quoted; else if (c == ',' && !quoted) { fields.Add(field.ToString()); field.Clear(); } else field.Append(c); }
        fields.Add(field.ToString()); return fields.ToArray();
    }
}
