"""Harness-neutral, locally rendered agent notification."""

load("render.star", "render")

def main(config):
    event = config.str("event", "attention")
    color = {"attention": "#ffb347", "complete": "#68d391", "error": "#fc8181"}.get(event, "#ffb347")
    label = {"attention": "INPUT", "complete": "DONE", "error": "ERROR"}.get(event, "INPUT")
    source = config.str("source", "agent").upper()[:10]
    return render.Root(
        delay = 100,
        child = render.Box(
            color = "#000000",
            child = render.Column(
                expanded = True,
                main_align = "space_evenly",
                cross_align = "center",
                children = [
                    render.Text(source, font = "tom-thumb", color = "#b7c5d8"),
                    render.Text(label, font = "tb-8", color = color),
                    render.Marquee(
                        width = 64,
                        offset_start = 32,
                        offset_end = 32,
                        child = render.Text(config.str("message", "Agent needs your attention"), font = "tom-thumb"),
                    ),
                ],
            ),
        ),
    )
