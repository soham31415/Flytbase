// Placeholder: shows the health check. The real UI polls /api/state (Step 4).
fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    document.getElementById("health").textContent =
      `Server ok. Live mode: ${h.live_mode ? "on" : "off"}.`;
  })
  .catch(() => {
    document.getElementById("health").textContent = "Server unreachable.";
  });
