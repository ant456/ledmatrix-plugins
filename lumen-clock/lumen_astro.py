"""Sun and moon for a place on Earth: positions, rise / set times, moon phase.

Pure maths, no network. Low-precision formulas (NOAA solar position, a short Meeus series for the
moon) that are good to a minute or two for the sun and a few minutes for the moon, which is plenty
for a 128x32 panel. Every time is a UTC ``datetime``; callers convert to local time.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

UTC = timezone.utc
RAD = math.pi / 180.0
SYNODIC = 29.530588853

PHASE_NAMES = ("NEW MOON", "WAXING CRESCENT", "FIRST QUARTER", "WAXING GIBBOUS",
               "FULL MOON", "WANING GIBBOUS", "LAST QUARTER", "WANING CRESCENT")


def julian(dt: datetime) -> float:
    """Julian day for an aware datetime."""
    dt = dt.astimezone(UTC)
    return dt.timestamp() / 86400.0 + 2440587.5


def _sun_coords(jd: float) -> Tuple[float, float, float]:
    """Sun's (declination rad, right ascension rad, equation-of-time minutes) -- NOAA, good to ~1 arcmin."""
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = m * RAD
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app = true_long - 0.00569 - 0.00478 * math.sin(omega * RAD)
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(omega * RAD)
    ra = math.atan2(math.cos(eps * RAD) * math.sin(app * RAD), math.cos(app * RAD))
    dec = math.asin(math.sin(eps * RAD) * math.sin(app * RAD))
    y = math.tan(eps * RAD / 2) ** 2
    eq = 4.0 * (y * math.sin(2 * l0 * RAD) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0 * RAD)
                - 0.5 * y * y * math.sin(4 * l0 * RAD) - 1.25 * e * e * math.sin(2 * mr)) / RAD
    return dec, ra, eq


def _gmst(jd: float) -> float:
    """Greenwich mean sidereal time in radians."""
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t
    return (g % 360.0) * RAD


def _altaz(ra: float, dec: float, jd: float, lat: float, lon: float) -> Tuple[float, float]:
    """(altitude, azimuth) in degrees; azimuth measured from north through east."""
    h = _gmst(jd) + lon * RAD - ra
    la = lat * RAD
    sin_alt = math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(h)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(-math.sin(h) * math.cos(dec), math.cos(la) * math.sin(dec) - math.sin(la) * math.cos(dec) * math.cos(h))
    return alt / RAD, (az / RAD) % 360.0


def sun_altaz(dt: datetime, lat: float, lon: float) -> Tuple[float, float]:
    jd = julian(dt)
    dec, ra, _ = _sun_coords(jd)
    return _altaz(ra, dec, jd, lat, lon)


def _moon_coords(jd: float) -> Tuple[float, float, float, float]:
    """Moon's (ecliptic longitude deg, ecliptic latitude deg, right ascension rad, declination rad).

    Main terms of Meeus ch. 47; accurate to roughly 0.3 degree, i.e. a few minutes of rise / set.
    """
    t = (jd - 2451545.0) / 36525.0
    lp = (218.3164477 + 481267.88123421 * t) % 360.0
    d = (297.8501921 + 445267.1114034 * t) % 360.0
    m = (357.5291092 + 35999.0502909 * t) % 360.0
    mp = (134.9633964 + 477198.8675055 * t) % 360.0
    f = (93.2720950 + 483202.0175233 * t) % 360.0
    dr, mr, mpr, fr = d * RAD, m * RAD, mp * RAD, f * RAD
    lon = (lp + 6.289 * math.sin(mpr) + 1.274 * math.sin(2 * dr - mpr) + 0.658 * math.sin(2 * dr)
           + 0.214 * math.sin(2 * mpr) - 0.186 * math.sin(mr) - 0.114 * math.sin(2 * fr)
           + 0.059 * math.sin(2 * dr - 2 * mpr) + 0.057 * math.sin(2 * dr - mr - mpr)
           + 0.053 * math.sin(2 * dr + mpr) + 0.046 * math.sin(2 * dr - mr) - 0.041 * math.sin(mr - mpr)
           - 0.035 * math.sin(dr) - 0.030 * math.sin(mr + mpr)) % 360.0
    lat = (5.128 * math.sin(fr) + 0.281 * math.sin(mpr + fr) + 0.278 * math.sin(mpr - fr)
           + 0.173 * math.sin(2 * dr - fr) + 0.055 * math.sin(2 * dr - mpr + fr)
           + 0.046 * math.sin(2 * dr - mpr - fr) + 0.033 * math.sin(2 * dr + fr))
    eps = (23.439291 - 0.0130042 * t) * RAD
    lr, br = lon * RAD, lat * RAD
    ra = math.atan2(math.sin(lr) * math.cos(eps) - math.tan(br) * math.sin(eps), math.cos(lr))
    dec = math.asin(math.sin(br) * math.cos(eps) + math.cos(br) * math.sin(eps) * math.sin(lr))
    return lon, lat, ra, dec


def moon_altaz(dt: datetime, lat: float, lon: float) -> Tuple[float, float]:
    jd = julian(dt)
    _, _, ra, dec = _moon_coords(jd)
    alt, az = _altaz(ra, dec, jd, lat, lon)
    # parallax lowers the Moon by up to ~1 degree; fold in the typical value so rise / set are honest
    return alt - 0.95 * math.cos(alt * RAD), az


def moon_phase(dt: datetime) -> Tuple[float, float, str]:
    """(age 0..1 through the cycle, illuminated fraction 0..1, phase name)."""
    jd = julian(dt)
    sun_lon = (280.46646 + 36000.76983 * ((jd - 2451545.0) / 36525.0)
               + 1.914602 * math.sin((357.52911 + 35999.05029 * ((jd - 2451545.0) / 36525.0)) * RAD)) % 360.0
    moon_lon, moon_lat, _, _ = _moon_coords(jd)
    elong = (moon_lon - sun_lon) % 360.0                  # 0 new, 90 first quarter, 180 full, 270 last quarter
    illum = (1.0 - math.cos(elong * RAD)) / 2.0
    # the Moon's latitude and distance nudge the true phase angle slightly; ignore, it is under 1 %
    age = elong / 360.0
    return age, illum, PHASE_NAMES[int(((elong + 22.5) % 360.0) // 45.0)]


def next_phase_events(dt: datetime, count: int = 4) -> List[Tuple[str, datetime]]:
    """The next quarter-phase moments (NEW / FIRST / FULL / LAST) after ``dt``, found by stepping elongation."""
    names = ["NEW MOON", "FIRST QUARTER", "FULL MOON", "LAST QUARTER"]
    out: List[Tuple[str, datetime]] = []
    jd0 = julian(dt)

    def elong(jd: float) -> float:
        t = (jd - 2451545.0) / 36525.0
        sl = (280.46646 + 36000.76983 * t + 1.914602 * math.sin((357.52911 + 35999.05029 * t) * RAD)) % 360.0
        return (_moon_coords(jd)[0] - sl) % 360.0

    e0 = elong(jd0)
    target = (int(e0 // 90.0) + 1) % 4
    guess = jd0 + ((target * 90.0 - e0) % 360.0 or 360.0) / 12.19
    for _ in range(count):
        want = target * 90.0
        jd = guess
        for _ in range(6):                                  # Newton on elongation, ~12.19 deg / day
            diff = ((want - elong(jd) + 180.0) % 360.0) - 180.0
            jd += diff / 12.19
        out.append((names[target], datetime.fromtimestamp((jd - 2440587.5) * 86400.0, UTC)))
        target = (target + 1) % 4
        guess = jd + 7.38
    return out


def _find_crossings(alt_fn, start: datetime, hours: float, threshold: float, step_min: int = 10
                    ) -> Tuple[List[datetime], List[datetime]]:
    """Times in ``[start, start+hours]`` when the altitude crosses ``threshold``: (risings, settings)."""
    rises: List[datetime] = []
    sets: List[datetime] = []
    n = int(hours * 60 / step_min)
    prev_t = start
    prev_a = alt_fn(prev_t) - threshold
    for i in range(1, n + 1):
        t = start + timedelta(minutes=step_min * i)
        a = alt_fn(t) - threshold
        if (prev_a < 0) != (a < 0):
            lo, hi, alo = prev_t, t, prev_a
            for _ in range(12):                              # bisect to ~seconds
                mid = lo + (hi - lo) / 2
                am = alt_fn(mid) - threshold
                if (am < 0) == (alo < 0):
                    lo, alo = mid, am
                else:
                    hi = mid
            (rises if a >= 0 else sets).append(lo + (hi - lo) / 2)
        prev_t, prev_a = t, a
    return rises, sets


def sun_times(day_start: datetime, lat: float, lon: float) -> dict:
    """Sunrise / sunset / dawn / dusk / solar noon within the 24 h from ``day_start`` (aware). Missing = None."""
    f = lambda t: sun_altaz(t, lat, lon)[0]                  # noqa: E731
    out = {}
    r, s = _find_crossings(f, day_start, 24, -0.833)
    out["sunrise"], out["sunset"] = (r[0] if r else None), (s[0] if s else None)
    r, s = _find_crossings(f, day_start, 24, -6.0)
    out["dawn"], out["dusk"] = (r[0] if r else None), (s[0] if s else None)
    best, best_t = -91.0, None
    for i in range(0, 24 * 6):
        t = day_start + timedelta(minutes=10 * i)
        a = f(t)
        if a > best:
            best, best_t = a, t
    out["noon"] = best_t
    out["max_alt"] = best
    return out


def moon_times(day_start: datetime, lat: float, lon: float) -> dict:
    """Moonrise / moonset within the 24 h from ``day_start`` (either can be None: the Moon skips one a day)."""
    f = lambda t: moon_altaz(t, lat, lon)[0]                 # noqa: E731
    r, s = _find_crossings(f, day_start, 24, 0.0, step_min=15)
    return {"moonrise": r[0] if r else None, "moonset": s[0] if s else None}


def bright_limb_angle(dt: datetime, lat: float, lon: float) -> float:
    """Degrees clockwise from straight up to the direction of the Sun, as seen from the Moon on the sky.

    That is where the Moon's bright limb points: toward the lower right for an evening crescent (the Sun has
    just set in the west), toward the left in the morning. Computed from both bodies' altitude and azimuth, so it
    carries the tilt a real Moon has at this place and time.
    """
    alt_m, az_m = moon_altaz(dt, lat, lon)
    alt_s, az_s = sun_altaz(dt, lat, lon)
    dz = (az_s - az_m) * RAD
    am, as_ = alt_m * RAD, alt_s * RAD
    y = math.sin(dz) * math.cos(as_)
    x = math.cos(am) * math.sin(as_) - math.sin(am) * math.cos(as_) * math.cos(dz)
    return math.degrees(math.atan2(y, x)) % 360.0


def local(dt: Optional[datetime], tz) -> Optional[datetime]:
    return dt.astimezone(tz) if dt else None


def day_info(now: datetime, lat: float, lon: float, tz) -> dict:
    """Everything the screens need about the sky around ``now`` (aware). Times are aware datetimes in ``tz``.

    sun:   today's sunrise / sunset / dawn / dusk, tomorrow's sunrise, daylight length, noon altitude
    moon:  phase age / illuminated fraction / name, bright-limb tilt, next rise / set, the rise-to-set
           pass the Moon is in now (None when it is below the horizon), the next quarter phases
    """
    now_l = now.astimezone(tz)
    midnight = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    sun = sun_times(midnight, lat, lon)
    tomorrow = sun_times(midnight + timedelta(days=1), lat, lon)
    info = {k: local(sun.get(k), tz) for k in ("sunrise", "sunset", "dawn", "dusk")}
    info["next_sunrise"] = local(tomorrow.get("sunrise"), tz)
    info["max_alt"] = sun.get("max_alt", 45.0)
    sr, ss = info["sunrise"], info["sunset"]
    info["daylight"] = (ss - sr).total_seconds() if sr and ss else None

    start = now - timedelta(hours=30)
    f = lambda t: moon_altaz(t, lat, lon)[0]                 # noqa: E731
    rises, sets = _find_crossings(f, start, 60, 0.0, step_min=15)
    up = f(now) > 0.0
    prior_rises = [r for r in rises if r <= now]
    later_sets = [s for s in sets if s >= now]
    later_rises = [r for r in rises if r >= now]
    age, illum, name = moon_phase(now)
    moon = {"age": age, "illum": illum, "name": name, "up": up,
            "limb": bright_limb_angle(now, lat, lon),
            "next_rise": local(later_rises[0], tz) if later_rises else None,
            "next_set": local(later_sets[0], tz) if later_sets else None,
            "events": [(n, local(t, tz)) for n, t in next_phase_events(now, 4)]}
    if up:
        moon["pass"] = (local(prior_rises[-1], tz) if prior_rises else None,
                        local(later_sets[0], tz) if later_sets else None)
    else:
        moon["pass"] = None
    info["moon"] = moon
    return info
