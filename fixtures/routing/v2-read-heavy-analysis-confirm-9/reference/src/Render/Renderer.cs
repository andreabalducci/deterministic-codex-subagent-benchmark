namespace Render;
public sealed class Renderer(HttpClient client)
{
    public async Task<byte[]> Render(Job job, CancellationToken ct)
    {
        try { return await client.GetByteArrayAsync(job.Uri, ct); }
        catch (OperationCanceledException) { return Array.Empty<byte>(); }
    }
}
