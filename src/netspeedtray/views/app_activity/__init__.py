"""App Activity package.

The standalone App Activity window was retired in 2.0 — the Monitor's Network tab replaced it
(reusing the lightweight AppActivityWorker here for its per-app connection list). Only the worker
remains; import it directly: `from netspeedtray.views.app_activity.worker import AppActivityWorker`.
"""
