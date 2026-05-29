"""Google Maps URL → (lat, lng) extraction.

Two-stage lookup:
  1. Regex-scan the URL itself for the common embedded-coordinate patterns
     (no network call).
  2. If the URL is a known short-link host (goo.gl, maps.app.goo.gl, g.co),
     follow redirects with a single bounded GET and re-scan the final URL.

Returns None on any failure — extraction is best-effort, not a hard error.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

# Patterns tried in order. Each must capture (lat, lng) as groups 1 and 2.
#   @LAT,LNG       — appears in /maps/@..., /maps/place/.../@...
#   !3dLAT!4dLNG   — embedded marker pair inside the canonical place URL
#   q=LAT,LNG      — explicit query coordinate
#   ll=LAT,LNG     — legacy "look at" coordinate
_PATTERNS = (
	re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)"),
	re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)"),
	re.compile(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)"),
	re.compile(r"[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)"),
)

# Hosts that serve short links and need a redirect-follow to reveal the
# real coordinates. `*.goo.gl` is matched separately via endswith() so any
# regional subdomain is covered.
_SHORT_LINK_HOSTS = frozenset({"goo.gl", "maps.app.goo.gl", "g.co"})


def extract_lat_lng(url: str, timeout: float = 3.0) -> tuple[float, float] | None:
	"""Best-effort (lat, lng) extraction from a Google Maps URL.

	Args:
		url:     The URL pasted by the user. Whitespace is stripped.
		timeout: Total seconds budget for the short-link redirect-follow GET.

	Returns:
		(lat, lng) on success, both floats with the standard Earth bounds
		(-90..90, -180..180). None if no coordinates are recoverable.
	"""
	if not url:
		return None
	url = url.strip()
	if not url:
		return None

	found = _scan(url)
	if found:
		return found

	try:
		host = (urlparse(url).hostname or "").lower()
	except (ValueError, AttributeError):
		return None
	if host not in _SHORT_LINK_HOSTS and not host.endswith(".goo.gl"):
		return None

	try:
		resp = requests.get(url, allow_redirects=True, timeout=timeout)
	except requests.RequestException:
		return None

	# resp.url is the final URL after redirects. If the redirect chain
	# never resolved (e.g. 200 directly with no Location), this falls back
	# to the original short-link string and the regex will simply miss.
	return _scan(resp.url or "")


def _scan(url: str) -> tuple[float, float] | None:
	for pattern in _PATTERNS:
		match = pattern.search(url)
		if not match:
			continue
		try:
			lat = float(match.group(1))
			lng = float(match.group(2))
		except (TypeError, ValueError):
			continue
		if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
			return lat, lng
	return None
