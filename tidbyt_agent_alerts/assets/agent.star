"""Source-specific artwork, embedded by the local renderer."""
load("encoding/base64.star", "base64")
load("render.star", "render")

ART = base64.decode("__ARTWORK__")

def main(config):
    return render.Root(
        delay = 80,
        child = render.Box(
            color = "#000000",
            child = render.Column(
                expanded = True,
                main_align = "space_evenly",
                cross_align = "center",
                children = [
                    render.Image(src = ART, width = 22, height = 22),
                    render.Marquee(width = 64, offset_start = 32, offset_end = 32,
                                   child = render.Text(config.str("message", "Agent needs attention"), font = "tom-thumb"),
                                   align = "center"),
                ],
            ),
        ),
    )
