"""
Graph engine package.

The standalone Graph window was retired in 2.0 - the unified Monitor (views/monitor/) replaced it.
What remains here is the reusable, matplotlib-backed graph ENGINE that the Monitor's GraphHost drives
byte-for-byte: GraphRenderer / GraphDataWorker / GraphCoordinator / GraphLogic / DataRequest. Import
those submodules directly (e.g. `from netspeedtray.views.graph.renderer import GraphRenderer`); this
package no longer exports a window, so importing it stays cheap (no eager matplotlib load).
"""
