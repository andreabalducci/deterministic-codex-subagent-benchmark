namespace Exports;
public sealed class CustomerExporter(AppDb db)
{
    public async Task<IReadOnlyList<Row>> Export(CancellationToken ct)
    {
        var customers = await db.Customers.ToListAsync(ct);
        var rows = new List<Row>();
        foreach (var customer in customers)
        {
            var orders = await db.Orders.Where(x => x.CustomerId == customer.Id).CountAsync(ct);
            rows.Add(new Row(customer.Id, orders));
        }
        return rows;
    }
}
