# Tunni Bars

A reporter plug-in for [Glyphs](https://glyphsapp.com) that shows, drags and balances the *Tunni bars* of curve segments. A Tunni bar is the line between the two off-curve points (BCPs) of a curve segment, named after Tunni’s observation that the handle lines and this bar form a triangle that describes the shape of the curve.

## Credits

Tunni Bars is based on **[Tunni Lines](https://github.com/simoncozens/GlyphsPlugins/tree/master/TunniLines.glyphsReporter)**, the reporter plug-in by **[Simon Cozens](https://github.com/simoncozens)**, from his [GlyphsPlugins](https://github.com/simoncozens/GlyphsPlugins) repository (MIT license). Tunni Lines introduced showing the handle intersection and the handle percentages in the Edit View; thank you, Simon.

## Usage

The only addition to the interface is the menu item **View > Show Tunni Bars**. Once it is switched on:

* Every curve segment gets a Tunni bar: very subtle and dashed, as thin as the handle lines.
* If a segment (both of its on-curve points) or one of its handles is selected, its bar turns solid and shows the handle percentages (in whole percent) next to the handles, on the side facing away from the curve. 0% is a fully retracted handle, 100% is a handle reaching the intersection of the handle lines, like in Tunni Lines.
* The bar and the percentages are **blue** if the handles are balanced (same percentage, give or take 2 units, `BALANCE_THRESHOLD` in `plugin.py`) and **grey** if they are not. Colors adapt to the Edit View’s Dark Mode.
* With the Select tool, **drag a Tunni bar** to move both handles along their own lines. The bar stays parallel to itself, so the angle of the bar and the angles of both handles stay the same, and both BCPs scale by the same factor around the intersection of the handle lines. The drag stops at 0% and at 100%. Zoomed out, the bar follows the mouse more slowly, so that the full range from 0% to 100% takes about 200 screen pixels (`DRAG_SWEEP_PIXELS` in `plugin.py`); zoomed in far enough, it sticks to the cursor.
* With the Select tool, **double-click a Tunni bar** to balance the handles: both get the same percentage, while the handle angles stay as they are. The percentage is chosen so that the point at t=0.5 of the old segment is still on the new segment. Handle coordinates are rounded to 1 unit (`ROUNDING` in `plugin.py`); an already balanced segment is left alone.

A bar that is being dragged is drawn solid, even if its segment is not selected. Nodes and handles always keep priority: clicking within a node or handle picks that node, not the bar behind it. Each drag or balancing is a single undo step.

## Limitations

* A segment has no Tunni bar if one of its handles is fully retracted, if the handles are parallel, or if the handle lines intersect behind an on-curve point.
* Rounding on double-click is per coordinate. For handles that are not horizontal or vertical, it can move a BCP sub-unit off its handle line.
* A reporter has no mouse API in Glyphs. Tunni Bars therefore watches the mouse with a local event monitor, active only while the menu item is on and the Select tool is the current tool.

## Installation

Copy `TunniBars.glyphsPlugin` into the Glyphs plug-ins folder (*Glyphs > Settings > Addons > Plugins > Show in Finder*) and restart Glyphs.

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2016 Simon Cozens (Tunni Lines), (c) 2026 mekkablue.
