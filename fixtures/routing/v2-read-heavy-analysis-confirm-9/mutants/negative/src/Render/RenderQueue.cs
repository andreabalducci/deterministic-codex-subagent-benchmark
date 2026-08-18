namespace Render;
public sealed class RenderQueue(Channel<Job> channel, Renderer renderer)
{
    public async Task Run(CancellationToken stoppingToken)
    {
        await foreach (var job in channel.Reader.ReadAllAsync())
        {
            await renderer.Render(job, CancellationToken.None);
        }
    }
}
