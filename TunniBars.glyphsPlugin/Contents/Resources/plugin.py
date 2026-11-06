"""
Tunni Bars

View > Show Tunni Bars draws the Tunni bar (the line between the two handles) of every curve
segment in the Edit View. Subtle and dashed, unless the segment or one of its handles is
selected. Bars are draggable and double-clickable as soon as they are visible, with the
Select tool:

* Drag a Tunni bar to move both handles along their own lines, keeping the angle of the
  bar and the angles of the handles.
* Double-click a Tunni bar to balance the handles.

Based on the TunniLines reporter by Simon Cozens (MIT license).
"""

import math
import traceback

import objc
from AppKit import (
	NSBeep,
	NSBezierPath,
	NSColor,
	NSEvent,
	NSEventMaskLeftMouseDown,
	NSEventMaskLeftMouseDragged,
	NSEventMaskLeftMouseUp,
	NSEventMaskMouseMoved,
	NSEventTypeLeftMouseDown,
	NSEventTypeLeftMouseDragged,
	NSEventTypeLeftMouseUp,
	NSEventTypeMouseMoved,
	NSUserDefaults,
)
from Foundation import NSClassFromString, NSPoint, NSPointInRect
from GlyphsApp import Glyphs, GSOFFCURVE, LogError
from GlyphsApp.plugins import ReporterPlugin

# Handle coordinates are rounded to multiples of this value when balancing (in font units).
ROUNDING = 1.0

# Tolerances in screen pixels for picking a Tunni bar with the mouse.
# The bar is grabbable this far from its line. It yields to nodes, handles (HANDLE_MARGIN),
# and to any curve that is closer to the mouse than the bar, so standard selection keeps working.
BAR_TOLERANCE = 10.0
HANDLE_MARGIN = 3.0

DEBUG_KEY = "com.mekkablue.TunniBars.debug"

# A drag that has seen no event for this long is considered abandoned (a mouse-up got lost).
STALE_DRAG_SECONDS = 1.0

EPSILON = 1e-9

# Handles count as balanced if both are within this many units of the balanced position.
BALANCE_THRESHOLD = 2.0

# Screen pixels of mouse travel for dragging a Tunni bar from 0% to 100% (at most; zoomed in far enough, the bar sticks to the cursor).
DRAG_SWEEP_PIXELS = 200.0

# Distance in screen pixels between a handle and its percentage label.
LABEL_GAP = 6.0

# ---------------------------------------------------------------------------
# Geometry on plain (x, y) tuples, independent of Glyphs
# ---------------------------------------------------------------------------


def subtract(p, q):
	return (p[0] - q[0], p[1] - q[1])


def add(p, q):
	return (p[0] + q[0], p[1] + q[1])


def scaled(p, factor):
	return (p[0] * factor, p[1] * factor)


def cross(p, q):
	return p[0] * q[1] - p[1] * q[0]


def dot(p, q):
	return p[0] * q[0] + p[1] * q[1]


def length(p):
	return math.hypot(p[0], p[1])


def distance(p, q):
	return length(subtract(p, q))


def mix(p, q, t):
	return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def bezierPoint(a, b, c, d, t):
	"""Point on the cubic Bézier curve a, b, c, d at parameter t."""
	mt = 1.0 - t
	return (
		mt**3 * a[0] + 3 * mt**2 * t * b[0] + 3 * mt * t**2 * c[0] + t**3 * d[0],
		mt**3 * a[1] + 3 * mt**2 * t * b[1] + 3 * mt * t**2 * c[1] + t**3 * d[1],
	)


def distanceToLineSegment(point, start, end):
	direction = subtract(end, start)
	squaredLength = dot(direction, direction)
	if squaredLength < EPSILON:
		return distance(point, start)
	t = max(0.0, min(1.0, dot(subtract(point, start), direction) / squaredLength))
	return distance(point, mix(start, end, t))


def distanceToCurve(point, a, b, c, d, samples=24):
	"""Distance from point to the cubic Bézier curve a, b, c, d (approximated by a polyline)."""
	previous = a
	best = float("inf")
	for step in range(1, samples + 1):
		current = bezierPoint(a, b, c, d, step / samples)
		best = min(best, distanceToLineSegment(point, previous, current))
		previous = current
	return best


def tunniPoint(a, b, c, d):
	"""
	Intersection of the handle lines a-b and d-c, plus the handle percentages
	(0.0 = handle fully retracted, 1.0 = handle reaches the intersection).
	Returns (intersection, percentageOfFirstHandle, percentageOfSecondHandle),
	or None if there is no Tunni bar: a retracted handle (no direction),
	parallel handles, or an intersection behind either on-curve point.
	"""
	u = subtract(b, a)
	v = subtract(c, d)
	if length(u) < EPSILON or length(v) < EPSILON:
		return None
	denominator = cross(u, v)
	if abs(denominator) < 1e-6 * length(u) * length(v):
		return None
	w = subtract(d, a)
	t1 = cross(w, v) / denominator  # a + t1 * u == intersection
	t2 = cross(w, u) / denominator  # d + t2 * v == intersection
	if t1 <= 0.0 or t2 <= 0.0:
		return None
	intersection = add(a, scaled(u, t1))
	return intersection, 1.0 / t1, 1.0 / t2


def handlesAreBalanced(a, d, tunni, threshold=BALANCE_THRESHOLD):
	"""
	True if both handles are at the same percentage, give or take `threshold` units:
	each handle is at most that far from the position the other handle's percentage would give it.
	"""
	intersection, percentage1, percentage2 = tunni
	difference = abs(percentage1 - percentage2)
	return difference * max(distance(a, intersection), distance(d, intersection)) <= threshold


def labelPlacement(onCurve, handle, otherOnCurve):
	"""
	Where to draw the percentage label of a handle: next to the handle, on the side of the handle
	line that faces away from the curve. Returns (direction, alignment): the unit vector from the
	handle towards the label, and the Glyphs text alignment that puts the text beyond that point.
	"""
	handleVector = subtract(handle, onCurve)
	handleLength = length(handleVector)
	normal = (-handleVector[1] / handleLength, handleVector[0] / handleLength)
	if dot(normal, subtract(otherOnCurve, onCurve)) > 0:  # the curve is on that side, so go the other way
		normal = (-normal[0], -normal[1])
	horizontal = 1 if normal[0] > 0.38 else -1 if normal[0] < -0.38 else 0
	vertical = 1 if normal[1] > 0.38 else -1 if normal[1] < -0.38 else 0
	alignment = {
		(1, 0): "left",
		(-1, 0): "right",
		(0, 1): "bottomcenter",
		(0, -1): "topcenter",
		(1, 1): "bottomleft",
		(1, -1): "topleft",
		(-1, 1): "bottomright",
		(-1, -1): "topright",
	}[(horizontal, vertical)]
	return normal, alignment


# --- dragging -------------------------------------------------------------


def dragStateForBar(a, b, c, d):
	"""
	Everything needed to drag the Tunni bar of the segment a, b, c, d,
	or None if the segment has no draggable Tunni bar.

	Moving the bar parallel to itself, with both handles staying on their lines,
	scales the triangle intersection-b-c about the intersection. That keeps the
	angle of the bar and the angles of both handles, and moves both BCPs by the
	same factor.
	"""
	tunni = tunniPoint(a, b, c, d)
	if tunni is None:
		return None
	intersection, percentage1, percentage2 = tunni
	bar = subtract(c, b)
	barLength = length(bar)
	if barLength < EPSILON:
		return None
	normal = (-bar[1] / barLength, bar[0] / barLength)
	offset = dot(subtract(b, intersection), normal)  # signed distance of the bar from the intersection
	if abs(offset) < EPSILON:
		return None
	# stop at fully retracted handles (0%)
	maxFactor = float("inf")
	for percentage in (percentage1, percentage2):
		if percentage < 1.0:
			maxFactor = min(maxFactor, 1.0 / (1.0 - percentage))
	# how far the bar travels between 0% and 100%, in units
	sweep = (abs(dot(subtract(a, intersection), normal)) + abs(dot(subtract(d, intersection), normal))) / 2
	return {
		"intersection": intersection,
		"b": b,
		"c": c,
		"normal": normal,
		"offset": offset,
		"maxFactor": maxFactor,
		"sweep": sweep,
	}


def dragGain(state, scale, sweepPixels=DRAG_SWEEP_PIXELS):
	"""
	How much of the mouse movement the bar follows. At 1.0 the bar sticks to the cursor, which
	is fine when zoomed in, but when zoomed out the bar only needs to travel a few pixels to
	sweep from 0% to 100%, so the handles would jump. The gain slows the drag down so that the
	full sweep takes about `sweepPixels` screen pixels, and never speeds it up.
	"""
	return min(1.0, state["sweep"] * scale / sweepPixels)


def movementLimits(state):
	"""The smallest and the largest movement of the bar: handles at 100% (both on the intersection) and at 0%."""
	atIntersection = -state["offset"]
	atRetracted = (state["maxFactor"] - 1.0) * state["offset"]
	return min(atIntersection, atRetracted), max(atIntersection, atRetracted)


def advancedMovement(state, movement, mouseStep, scale):
	"""
	The movement of the bar after the mouse moved by `mouseStep` (x, y in units) since the last
	event. The movement is accumulated step by step and clamped at every step, so a bar that
	reached 0% or 100% leaves that limit again as soon as the mouse moves back.
	"""
	low, high = movementLimits(state)
	movement += dot(mouseStep, state["normal"]) * dragGain(state, scale)
	return max(low, min(high, movement))


def draggedHandles(state, movement):
	"""
	New (b, c) after moving the bar by `movement` (a signed distance along the bar’s
	normal) from where it was when the drag started.
	"""
	factor = (state["offset"] + movement) / state["offset"]
	factor = max(0.0, min(state["maxFactor"], factor))
	intersection = state["intersection"]
	newB = add(intersection, scaled(subtract(state["b"], intersection), factor))
	newC = add(intersection, scaled(subtract(state["c"], intersection), factor))
	return newB, newC


# --- balancing ------------------------------------------------------------


def balancedHandles(a, b, c, d, rounding=ROUNDING):
	"""
	New (b, c) with both handles at the same percentage along their lines,
	such that the curve still passes through the point at t=0.5 of the current
	segment (at whatever t that point ends up on the balanced segment).
	Handle angles stay as they are. Coordinates are rounded to multiples of
	`rounding` (0 or None for no rounding).
	Returns None if the segment has no Tunni bar or no such percentage exists.

	With both handles at percentage r, the curve is
	    B(t) = Q(t) + r * 3t(1-t) * (I - L(t))
	where I is the intersection of the handle lines, L(t) = (1-t) a + t d, and
	Q(t) = (1-t)^2 (1+2t) a + t^2 (3-2t) d. The curve passes through the target
	point P if P - Q(t) is parallel to I - L(t); that is a root of
	f(t) = (P - Q(t)) x (I - L(t)). Then r follows from either coordinate.
	"""
	tunni = tunniPoint(a, b, c, d)
	if tunni is None:
		return None
	intersection = tunni[0]
	target = bezierPoint(a, b, c, d, 0.5)

	def offsets(t):
		q = add(scaled(a, (1 - t)**2 * (1 + 2 * t)), scaled(d, t**2 * (3 - 2 * t)))
		return subtract(target, q), subtract(intersection, mix(a, d, t))

	def f(t):
		toTarget, toIntersection = offsets(t)
		return cross(toTarget, toIntersection)

	def percentageAt(t):
		toTarget, toIntersection = offsets(t)
		squaredLength = dot(toIntersection, toIntersection)
		if squaredLength < EPSILON:
			return None
		return dot(toTarget, toIntersection) / (3 * t * (1 - t) * squaredLength)

	steps = 400
	tMin, tMax = 0.05, 0.95
	candidates = []
	previousT = tMin
	previousValue = f(previousT)
	for step in range(1, steps + 1):
		t = tMin + (tMax - tMin) * step / steps
		value = f(t)
		if previousValue == 0.0 or previousValue * value < 0.0:
			low, high = previousT, t
			lowValue = previousValue
			for _ in range(60):
				middle = (low + high) / 2
				middleValue = f(middle)
				if lowValue * middleValue <= 0.0:
					high = middle
				else:
					low, lowValue = middle, middleValue
			root = (low + high) / 2
			percentage = percentageAt(root)
			if percentage is not None and 0.0 < percentage <= 1.0:
				candidates.append((abs(root - 0.5), percentage))
		previousT, previousValue = t, value
	if not candidates:
		return None
	percentage = min(candidates)[1]

	newB = mix(a, intersection, percentage)
	newC = mix(d, intersection, percentage)
	if rounding:
		newB = tuple(round(value / rounding) * rounding for value in newB)
		newC = tuple(round(value / rounding) * rounding for value in newC)
	return newB, newC


# ---------------------------------------------------------------------------
# Glyphs helpers
# ---------------------------------------------------------------------------


def positionOf(node):
	return (node.position.x, node.position.y)


def curveSegments(layer):
	"""Yields (a, b, c, d) GSNodes for every curve segment: on-curve, two BCPs, on-curve."""
	for path in layer.paths:
		nodes = path.nodes
		count = len(nodes)
		if count < 4:
			continue
		for index, a in enumerate(nodes):
			if a.type == GSOFFCURVE:
				continue
			if path.closed:
				b, c, d = (nodes[(index + offset) % count] for offset in (1, 2, 3))
			elif index + 3 < count:
				b, c, d = nodes[index + 1], nodes[index + 2], nodes[index + 3]
			else:
				continue
			if b.type == GSOFFCURVE and c.type == GSOFFCURVE and d.type != GSOFFCURVE:
				yield a, b, c, d


def segmentIsActive(selection, a, b, c, d):
	"""A segment is active if it is selected (both its on-curve points) or one of its handles is."""
	return b in selection or c in selection or (a in selection and d in selection)


def tunniColor(balanced, dark, alpha, hover=False):
	"""
	Blue for balanced handles, grey for unbalanced ones, each in a variant for light and dark backgrounds.
	The hovered bar is fully opaque, and brighter on dark backgrounds.
	"""
	if balanced:
		if dark:
			red, green, blue = (0.55, 0.75, 1.0) if hover else (0.35, 0.6, 1.0)
		else:
			red, green, blue = (0.0, 0.35, 1.0)
	else:
		red = green = blue = (0.95 if hover else 0.75) if dark else 0.45
	if hover:
		alpha = 1.0
	return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, alpha)


def handleSizeInPixels():
	"""Node size from the preferences, the same way ReporterPlugin.getHandleSize() reads it."""
	size = NSUserDefaults.standardUserDefaults().integerForKey_("GSHandleSize")
	if size == 0:
		return 5.0
	elif size == 2:
		return 10.0
	return 7.0


# ---------------------------------------------------------------------------
# Reporter: View > Show Tunni Bars
# ---------------------------------------------------------------------------


class TunniBars(ReporterPlugin):

	barDrag = None  # state of the Tunni bar drag in progress, if any
	hoverBar = None  # (a, b, c, d) nodes of the Tunni bar under the mouse, if any

	@objc.python_method
	def settings(self):
		self.menuName = Glyphs.localize({
			'en': 'Tunni Bars',
			'de': 'Tunni-Balken',
		})

	@objc.python_method
	def currentEditView(self):
		"""
		The Edit View of the current tab. self.controller is only reliably set while Glyphs is
		drawing and can be None in mouse events, so fall back to the current tab of the font.
		"""
		controller = getattr(self, "controller", None)
		if controller is not None:
			return controller.graphicView()
		font = Glyphs.font
		tab = font.currentTab if font is not None else None
		return tab.graphicView() if tab is not None else None

	@objc.python_method
	def conditionsAreMetForDrawing(self):
		"""Draw only while the Select tool is active: no bars in text mode, preview, or with the Hand tool (space bar)."""
		try:
			currentController = self.currentEditView().window().windowController()
		except AttributeError:
			# view or window not available (e.g. during window setup/teardown)
			return False
		if currentController:
			tool = currentController.toolDrawDelegate()
			return bool(tool.isKindOfClass_(NSClassFromString("GlyphsToolSelect")))
		return False

	@objc.python_method
	def foreground(self, layer):
		if not self.conditionsAreMetForDrawing():
			return  # bars only with the Select tool: not in text mode, preview or Hand tool
		scale = self.getScale()
		selection = set(layer.selection)
		for a, b, c, d in curveSegments(layer):
			points = [positionOf(node) for node in (a, b, c, d)]
			tunni = tunniPoint(*points)
			if tunni is None:
				continue
			percentage1, percentage2 = tunni[1], tunni[2]
			active = segmentIsActive(selection, a, b, c, d)
			if not active and self.barDrag is not None:
				active = self.barDrag.get("bar") == (a, b, c, d)  # the bar being dragged is solid, too

			balanced = handlesAreBalanced(points[0], points[3], tunni)
			dark = bool(self.black)

			# as thin as the handle lines; very subtle and dashed unless active
			bar = NSBezierPath.bezierPath()
			bar.moveToPoint_(NSPoint(*points[1]))
			bar.lineToPoint_(NSPoint(*points[2]))
			bar.setLineWidth_(1.0 / scale)
			hover = self.hoverBar == (a, b, c, d)
			if hover:
				active = True
			if active:
				barColor = tunniColor(balanced, dark, 0.6 if balanced else 0.7, hover)
			else:
				barColor = tunniColor(balanced, dark, 0.25)
				bar.setLineDash_count_phase_([3.0 / scale, 3.0 / scale], 2, 0)
			barColor.set()
			bar.stroke()

			if active:
				# handle percentages, in whole percent, beside the handles and away from the curve
				textColor = tunniColor(balanced, dark, 1.0)
				for onCurve, handle, otherOnCurve, percentage in (
					(points[0], points[1], points[3], percentage1),
					(points[3], points[2], points[0], percentage2),
				):
					direction, alignment = labelPlacement(onCurve, handle, otherOnCurve)
					gap = LABEL_GAP / scale
					self.drawTextAtPoint(
						"%.0f%%" % (100 * percentage),
						NSPoint(handle[0] + direction[0] * gap, handle[1] + direction[1] * gap),
						fontSize=10,
						fontColor=textColor,
						align=alignment,
					)

	# --- mouse handling ----------------------------------------------------
	# A reporter has no mouse API, so a local event monitor watches the mouse
	# events of this Edit View, and swallows only the ones that hit a Tunni bar.

	@objc.python_method
	def start(self):
		self.barDrag = None
		self.hoverBar = None
		mask = NSEventMaskLeftMouseDown | NSEventMaskLeftMouseDragged | NSEventMaskLeftMouseUp | NSEventMaskMouseMoved
		self.eventMonitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self.handleEvent)

	@objc.python_method
	def handleEvent(self, event):
		"""Event monitor callback. Returns the event to pass it on, None to swallow it."""
		try:
			eventType = event.type()
			if Glyphs.defaults.get(DEBUG_KEY) and eventType != NSEventTypeMouseMoved:
				# Switch on in the Macro Panel: Glyphs.defaults["com.mekkablue.TunniBars.debug"] = True
				print("TunniBars %.3f type=%s clicks=%s buttons=%s drag=%s" % (
					event.timestamp(), eventType, event.clickCount(), NSEvent.pressedMouseButtons(), self.barDrag is not None))
			if eventType == NSEventTypeLeftMouseDown:
				if self.mouseDown(event):
					return None
			elif eventType == NSEventTypeLeftMouseDragged:
				if self.barDrag is not None:
					self.mouseDragged(event)
					return None
			elif eventType == NSEventTypeLeftMouseUp:
				if self.barDrag is not None:
					self.mouseUp(event)
					return None
			elif eventType == NSEventTypeMouseMoved:
				self.updateHover(event)
		except Exception:
			LogError(traceback.format_exc())
			if self.barDrag is not None:
				if event.type() == NSEventTypeLeftMouseDragged:
					return None  # keep swallowing the drag; Glyphs must not start a marquee. Mouse-up ends it.
				if event.type() == NSEventTypeLeftMouseUp:
					self.barDrag = None
					return None
			self.barDrag = None
		return event

	@objc.python_method
	def editViewForEvent(self, event):
		"""The Edit View if this reporter is switched on, the Select tool is active, and the event is in the view."""
		if not any(reporter.className() == self.className() for reporter in Glyphs.activeReporters):
			return None
		view = self.currentEditView()
		if view is None:
			return None
		window = view.window()
		if window is None or event.window() != window:
			return None
		windowController = window.windowController()
		if windowController is None:
			return None
		if not windowController.toolDrawDelegate().isKindOfClass_(NSClassFromString("GlyphsToolSelect")):
			return None
		if not NSPointInRect(view.convertPoint_fromView_(event.locationInWindow(), None), view.bounds()):
			return None
		return view

	@objc.python_method
	def barAtLocation(self, layer, location, scale):
		"""The (a, b, c, d) nodes of the Tunni bar under the mouse, or None."""
		position = (location.x, location.y)
		nodeRadius = (handleSizeInPixels() / 2 + HANDLE_MARGIN) / scale
		for path in layer.paths:
			for node in path.nodes:
				if distance(position, positionOf(node)) <= nodeRadius:
					return None  # leave nodes and handles to the Select tool
		tolerance = BAR_TOLERANCE / scale
		bestBar, bestDistance = None, tolerance
		curveDistance = float("inf")
		for a, b, c, d in curveSegments(layer):
			points = [positionOf(node) for node in (a, b, c, d)]
			curveDistance = min(curveDistance, distanceToCurve(position, *points))
			if tunniPoint(*points) is None:
				continue
			barDistance = distanceToLineSegment(position, points[1], points[2])
			if barDistance <= bestDistance:
				bestBar, bestDistance = (a, b, c, d), barDistance
		if bestBar is not None and curveDistance < bestDistance:
			return None  # a curve is closer than the bar: leave it to the Select tool
		return bestBar

	@objc.python_method
	def updateHover(self, event):
		"""Remember the Tunni bar that can be grabbed at the mouse position, to draw it highlighted."""
		if self.barDrag is not None:
			return
		bar = None
		if self.conditionsAreMetForDrawing():
			view = self.editViewForEvent(event)
			layer = view.activeLayer() if view is not None else None
			if layer is not None:
				bar = self.barAtLocation(layer, view.getActiveLocation_(event), view.scale())
		if bar != self.hoverBar:
			self.hoverBar = bar
			Glyphs.redraw()

	@objc.python_method
	def mouseDown(self, event):
		"""Returns True if the click hit a Tunni bar and was taken care of here."""
		drag = self.barDrag
		if drag is not None and event.timestamp() - drag["lastEventTime"] < STALE_DRAG_SECONDS:
			# Another mouse-down while a drag is going on (the button is still held): never restart or
			# drop the drag, and don't let it reach Glyphs, which would start a marquee selection.
			drag["lastEventTime"] = event.timestamp()
			return True
		self.barDrag = None
		if not self.conditionsAreMetForDrawing():
			return False
		view = self.editViewForEvent(event)
		if view is None:
			return False
		layer = view.activeLayer()
		if layer is None:
			return False
		location = view.getActiveLocation_(event)
		bar = self.barAtLocation(layer, location, view.scale())
		if bar is None:
			return False
		if event.clickCount() >= 2:
			self.balanceBar(layer, bar)
			self.barDrag = {"layer": layer, "state": None, "undoOpen": False, "lastEventTime": event.timestamp()}  # swallow the rest of this click
		else:
			windowPoint = event.locationInWindow()
			self.barDrag = {
				"layer": layer,
				"view": view,
				"bar": bar,
				"state": dragStateForBar(*[positionOf(node) for node in bar]),
				"previousPoint": (windowPoint.x, windowPoint.y),
				"movement": 0.0,
				"undoOpen": False,
				"lastEventTime": event.timestamp(),
			}
		return True

	@objc.python_method
	def mouseDragged(self, event):
		drag = self.barDrag
		drag["lastEventTime"] = event.timestamp()
		if drag["state"] is None:
			return
		# Only the raw mouse movement between two events counts, in window points, divided by the zoom.
		# Positions from getActiveLocation_() are not used here: they can jump from one drag event to
		# the next, and a jump of a few units is all it takes to snap the handles to 0% or 100%.
		scale = drag["view"].scale()  # self.controller can be None while dragging
		windowPoint = event.locationInWindow()
		previousPoint = drag["previousPoint"]
		drag["previousPoint"] = (windowPoint.x, windowPoint.y)
		mouseStep = ((windowPoint.x - previousPoint[0]) / scale, (windowPoint.y - previousPoint[1]) / scale)
		drag["movement"] = advancedMovement(drag["state"], drag["movement"], mouseStep, scale)
		newB, newC = draggedHandles(drag["state"], drag["movement"])
		if not drag["undoOpen"]:
			drag["layer"].parent.beginUndo()
			drag["undoOpen"] = True
		a, b, c, d = drag["bar"]
		b.position = NSPoint(*newB)
		c.position = NSPoint(*newC)
		Glyphs.redraw()

	@objc.python_method
	def mouseUp(self, event):
		drag = self.barDrag
		self.barDrag = None
		if drag["undoOpen"]:
			drag["layer"].parent.endUndo()

	@objc.python_method
	def balanceBar(self, layer, bar):
		a, b, c, d = bar
		current = (positionOf(b), positionOf(c))
		balanced = balancedHandles(positionOf(a), current[0], current[1], positionOf(d))
		if balanced is None:
			NSBeep()
			return
		if distance(balanced[0], current[0]) < ROUNDING and distance(balanced[1], current[1]) < ROUNDING:
			return  # already balanced within the rounding threshold
		glyph = layer.parent
		glyph.beginUndo()
		try:
			b.position = NSPoint(*balanced[0])
			c.position = NSPoint(*balanced[1])
		finally:
			glyph.endUndo()
		Glyphs.redraw()
