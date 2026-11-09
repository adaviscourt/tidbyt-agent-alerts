import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { basename } from "node:path"
import { homedir } from "node:os"

// Defaults below are replaced when the plugin is installed by the setup wizard.
const managed = null

export default Plugin.define({
  id: "tidbyt-agent-alerts",
  setup(ctx) {
    const controller = new AbortController()
    const launcher = managed?.launcher || (typeof ctx.options.launcher === "string"
      ? ctx.options.launcher : `${homedir()}/.local/bin/tidbyt-agent-alerts`)
    const selected = managed?.events || ctx.options.events || "attention"
    const types = ["permission.asked", "form.created", "session.execution.succeeded"]
    void (async () => {
      for await (const event of ctx.event.subscribe({ types, signal: controller.signal })) {
        // Every location has its own plugin instance, but the event stream is server-wide.
        if (!event.location || event.location.directory !== ctx.location.directory) continue
        const complete = event.type === "session.execution.succeeded"
        if (complete ? selected !== "both" && selected !== "complete" : selected !== "both" && selected !== "attention") continue
        const args = []
        const config = managed?.config || ctx.options.config
        if (typeof config === "string") args.push("--config", config)
        args.push("notify", "--source", "opencode", "--event", complete ? "complete" : "attention",
          "--project", basename(event.location.directory) || "OpenCode")
        try {
          const child = spawn(launcher, args, { detached: true, stdio: "ignore" })
          child.on("error", () => {})
          child.unref()
        } catch { /* Alerts must never interrupt OpenCode. */ }
      }
    })().catch(() => {})
    return () => controller.abort()
  },
})
