namespace Exports;
public sealed class ExportController(CustomerExporter exporter)
{
    public async void Start(CancellationToken ct) => await exporter.Export(ct);
}
