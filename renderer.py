import math
from datetime import datetime

import pygame

import config


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def load_fonts():
    def sf(name, size, bold=False):
        try:
            return pygame.font.SysFont(name, size, bold=bold)
        except Exception:
            return pygame.font.Font(None, size)

    return {
        "title":        sf("dejavusans", 26, bold=True),   # location name
        "subtitle":     sf("dejavusans", 17),              # date + clock
        "temp":         sf("dejavusans", 80, bold=True),   # main temperature
        "condition":    sf("dejavusans", 21),              # weather description
        "feels":        sf("dejavusans", 17),              # feels-like line
        "stat":         sf("dejavusans", 20, bold=True),   # inline stat rows
        "day":          sf("dejavusans", 15),              # forecast day label
        "day_temp":     sf("dejavusans", 18, bold=True),   # forecast hi/lo
        "card_temp":    sf("dejavusans", 22, bold=True),   # hourly card temp
        "small":        sf("dejavusans", 14),              # hints, stale label
        "overlay":      sf("dejavusans", 28, bold=True),   # loading/error
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blit_c(surface, surf, cx, y):
    surface.blit(surf, (cx - surf.get_width() // 2, y))


def _blit_r(surface, surf, rx, y):
    surface.blit(surf, (rx - surf.get_width(), y))


# ---------------------------------------------------------------------------
# Weather icons  (pure pygame.draw, size-relative coordinates)
# ---------------------------------------------------------------------------

def _cloud(surface, cx, cy, size, color):
    r0 = int(size * 0.22)
    rl = int(size * 0.15)
    rr = int(size * 0.18)
    ox = int(size * 0.20)
    oy = int(size * 0.06)
    bx = cx - ox - rl
    bw = cx + ox + rr - bx
    pygame.draw.rect(surface, color, (bx, cy + int(size * 0.02), bw, r0 + int(size * 0.06)))
    pygame.draw.circle(surface, color, (cx - ox, cy + oy), rl)
    pygame.draw.circle(surface, color, (cx + ox, cy),      rr)
    pygame.draw.circle(surface, color, (cx,      cy - oy), r0)


def _sun_disk(surface, cx, cy, size):
    r = int(size * 0.38)
    pygame.draw.circle(surface, config.SUN_COLOR, (cx, cy), r)
    # Soft inner highlight
    hl = int(size * 0.14)
    hl_color = (min(255, config.SUN_COLOR[0] + 35),
                min(255, config.SUN_COLOR[1] + 35),
                min(255, config.SUN_COLOR[2] + 50))
    pygame.draw.circle(surface, hl_color,
                       (cx - int(size * 0.10), cy - int(size * 0.10)), hl)


def _draw_icon_clear_day(surface, cx, cy, size):
    _sun_disk(surface, cx, cy, size)


def _draw_icon_clear_night(surface, cx, cy, size):
    r_big = int(size * 0.32)
    r_cut = int(size * 0.26)
    ox    = int(size * 0.16)
    oy    = int(size * 0.06)
    pygame.draw.circle(surface, config.MOON_COLOR, (cx, cy), r_big)
    pygame.draw.circle(surface, config.BG_COLOR,
                       (cx + ox, cy - oy), r_cut)
    # Stars
    for sx, sy, sr in [
        (cx + int(size * 0.38), cy - int(size * 0.28), max(1, size // 30)),
        (cx + int(size * 0.22), cy - int(size * 0.42), max(1, size // 36)),
        (cx - int(size * 0.08), cy - int(size * 0.40), max(1, size // 36)),
    ]:
        pygame.draw.circle(surface, config.MOON_COLOR, (sx, sy), sr)


def _draw_icon_partly_cloudy(surface, cx, cy, size):
    # Sun disk (smaller, offset up-left)
    sun_r  = int(size * 0.26)
    sun_cx = cx - int(size * 0.15)
    sun_cy = cy - int(size * 0.18)
    pygame.draw.circle(surface, config.SUN_COLOR, (sun_cx, sun_cy), sun_r)
    _cloud(surface, cx + int(size * 0.06), cy + int(size * 0.12),
           int(size * 0.82), (195, 202, 215))


def _draw_icon_cloudy(surface, cx, cy, size):
    _cloud(surface, cx, cy, size, (175, 182, 195))


def _draw_icon_fog(surface, cx, cy, size):
    widths  = [0.68, 0.48, 0.62, 0.44]
    n       = len(widths)
    spacing = int(size * 0.18)
    lw      = max(3, size // 14)
    y0      = cy - (n * spacing) // 2
    for i, w in enumerate(widths):
        half = int(size * w / 2)
        y    = y0 + i * spacing
        pygame.draw.line(surface, config.FOG_COLOR,
                         (cx - half, y), (cx + half, y), lw)


def _draw_icon_drizzle(surface, cx, cy, size):
    _cloud(surface, cx, cy - int(size * 0.15), int(size * 0.82), (160, 168, 182))
    lw  = max(1, size // 22)
    dl  = int(size * 0.15)
    ang = math.radians(18)
    dx  = int(dl * math.sin(ang))
    dy  = int(dl * math.cos(ang))
    ys  = cy + int(size * 0.13)
    for x in [cx - int(size * 0.16), cx, cx + int(size * 0.16)]:
        pygame.draw.line(surface, config.RAIN_COLOR,
                         (x, ys), (x + dx, ys + dy), lw)


def _draw_icon_rain(surface, cx, cy, size):
    _cloud(surface, cx, cy - int(size * 0.13), size, (128, 138, 155))
    lw = max(2, size // 16)
    dl = int(size * 0.22)
    for x, ys in [
        (cx - int(size * 0.20), cy + int(size * 0.14)),
        (cx - int(size * 0.07), cy + int(size * 0.10)),
        (cx + int(size * 0.07), cy + int(size * 0.14)),
        (cx + int(size * 0.20), cy + int(size * 0.10)),
    ]:
        pygame.draw.line(surface, config.RAIN_COLOR, (x, ys), (x, ys + dl), lw)


def _draw_icon_snow(surface, cx, cy, size):
    _cloud(surface, cx, cy - int(size * 0.13), size, (162, 170, 185))
    fr  = int(size * 0.08)
    lw  = max(1, size // 24)
    for fx, fy in [
        (cx - int(size * 0.16), cy + int(size * 0.25)),
        (cx,                    cy + int(size * 0.29)),
        (cx + int(size * 0.16), cy + int(size * 0.25)),
    ]:
        for ang in [0, 45, 90, 135]:
            r = math.radians(ang)
            pygame.draw.line(surface, config.SNOW_COLOR,
                             (fx - int(fr * math.cos(r)), fy - int(fr * math.sin(r))),
                             (fx + int(fr * math.cos(r)), fy + int(fr * math.sin(r))), lw)


def _draw_icon_sleet(surface, cx, cy, size):
    _cloud(surface, cx, cy - int(size * 0.13), size, (128, 138, 155))
    lw  = max(2, size // 18)
    dl  = int(size * 0.17)
    fr  = int(size * 0.06)
    ys0 = cy + int(size * 0.13)
    for i, x in enumerate([cx - int(size*0.20), cx - int(size*0.07),
                            cx + int(size*0.07), cx + int(size*0.20)]):
        if i % 2 == 0:
            pygame.draw.line(surface, config.RAIN_COLOR,
                             (x, ys0), (x, ys0 + dl), lw)
        else:
            sy = ys0 + dl // 2
            for ang in [0, 45, 90, 135]:
                r = math.radians(ang)
                pygame.draw.line(surface, config.SNOW_COLOR,
                                 (x - int(fr * math.cos(r)), sy - int(fr * math.sin(r))),
                                 (x + int(fr * math.cos(r)), sy + int(fr * math.sin(r))), lw)


def _draw_icon_storm(surface, cx, cy, size):
    _cloud(surface, cx, cy - int(size * 0.10), size, (100, 106, 120))
    bx, by = cx - int(size * 0.06), cy + int(size * 0.08)
    pygame.draw.polygon(surface, config.STORM_COLOR, [
        (bx,                  by),
        (bx + int(size*0.17), by),
        (bx + int(size*0.05), by + int(size*0.15)),
        (bx + int(size*0.17), by + int(size*0.15)),
        (bx - int(size*0.05), by + int(size*0.38)),
        (bx + int(size*0.07), by + int(size*0.19)),
        (bx - int(size*0.05), by + int(size*0.19)),
    ])


_ICONS = {
    "clear_day":     _draw_icon_clear_day,
    "clear_night":   _draw_icon_clear_night,
    "partly_cloudy": _draw_icon_partly_cloudy,
    "cloudy":        _draw_icon_cloudy,
    "fog":           _draw_icon_fog,
    "drizzle":       _draw_icon_drizzle,
    "rain":          _draw_icon_rain,
    "snow":          _draw_icon_snow,
    "sleet":         _draw_icon_sleet,
    "storm":         _draw_icon_storm,
}


def draw_weather_icon(surface, cx, cy, size, icon_type):
    _ICONS.get(icon_type, _draw_icon_cloudy)(surface, cx, cy, size)


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

def _draw_background(surface):
    surface.fill(config.BG_COLOR)


def _draw_loading(surface, fonts):
    ov = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)
    ov.fill((248, 245, 240, 210))
    surface.blit(ov, (0, 0))
    surf = fonts["overlay"].render("Fetching weather…", True, config.ACCENT_COLOR)
    _blit_c(surface, surf, config.SCREEN_WIDTH // 2,
            (config.SCREEN_HEIGHT - surf.get_height()) // 2)


def _draw_error(surface, fonts):
    ov = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)
    ov.fill((248, 245, 240, 210))
    surface.blit(ov, (0, 0))
    for i, line in enumerate(["Could not reach weather service.", "Tap to retry."]):
        surf = fonts["overlay"].render(line, True, config.ERROR_COLOR)
        _blit_c(surface, surf, config.SCREEN_WIDTH // 2,
                config.SCREEN_HEIGHT // 2 - 25 + i * 45)


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def _draw_top_bar(surface, fonts):
    now = datetime.now()

    loc_surf  = fonts["title"].render(config.LOCATION_NAME, True, config.TEXT_PRIMARY)
    date_str  = now.strftime("%A, %d %B")
    clock_str = now.strftime("%H:%M")
    sub_surf  = fonts["subtitle"].render(f"{date_str}  ·  {clock_str}",
                                         True, config.TEXT_SECONDARY)
    total_h = loc_surf.get_height() + 3 + sub_surf.get_height()
    cx = config.SCREEN_WIDTH // 2
    y0 = (config.TOP_BAR_H - total_h) // 2
    _blit_c(surface, loc_surf, cx, y0)
    _blit_c(surface, sub_surf, cx, y0 + loc_surf.get_height() + 3)


def _draw_main_content(surface, fonts, data):
    if data is None:
        return

    # ── Left column: hero icon ──────────────────────────────────────────────
    icon_cx = config.LEFT_PANEL_W // 2
    icon_cy = config.TOP_BAR_H + config.MAIN_H // 2
    draw_weather_icon(surface, icon_cx, icon_cy, 130, data.icon_type)

    # ── Right column: temperature + stats ───────────────────────────────────
    rx  = config.LEFT_PANEL_W + 25
    rx2 = config.LEFT_PANEL_W + 295   # second-column position for paired stats
    y   = config.TOP_BAR_H + 18

    # Temperature
    temp_surf = fonts["temp"].render(f"{data.temp:.0f}°C", True, config.TEXT_PRIMARY)
    surface.blit(temp_surf, (rx, y))
    y += temp_surf.get_height() + 2

    # Condition
    cond_surf = fonts["condition"].render(data.condition, True, config.TEXT_SECONDARY)
    surface.blit(cond_surf, (rx, y))
    y += cond_surf.get_height() + 3

    # Feels like
    feels_surf = fonts["feels"].render(f"Feels like {data.feels_like:.0f}°C",
                                       True, config.TEXT_SECONDARY)
    surface.blit(feels_surf, (rx, y))
    y += feels_surf.get_height() + 22

    # ── Stat rows ────────────────────────────────────────────────────────────
    # Row 1: Sunrise | Sunset
    sun_surf = fonts["stat"].render(f"↑ {data.sunrise}", True, config.SUN_COLOR)
    set_surf = fonts["stat"].render(f"↓ {data.sunset}",  True, config.MOON_COLOR)
    surface.blit(sun_surf, (rx,  y))
    surface.blit(set_surf, (rx2, y))
    y += sun_surf.get_height() + 9

    # Row 2: Humidity | Wind
    hum_surf  = fonts["stat"].render(f"{data.humidity}%  humidity",
                                     True, config.RAIN_COLOR)
    wind_surf = fonts["stat"].render(
        f"{data.wind_speed:.0f} km/h {data.wind_dir_label}",
        True, config.TEXT_SECONDARY)
    surface.blit(hum_surf,  (rx,  y))
    surface.blit(wind_surf, (rx2, y))
    y += hum_surf.get_height() + 9

    # Row 3: Precipitation
    p_col  = config.RAIN_COLOR if data.precip_prob > 20 else config.TEXT_SECONDARY
    p_surf = fonts["stat"].render(f"{data.precip_prob}%  precip. chance", True, p_col)
    surface.blit(p_surf, (rx, y))


def _draw_forecast_strip(surface, fonts, data):
    if data is None:
        return

    y0     = config.FORECAST_Y
    h      = config.FORECAST_H
    cell_w = config.SCREEN_WIDTH // 7

    # Subtle separator above strip
    pygame.draw.rect(surface, config.DIVIDER_COLOR,
                     (0, y0, config.SCREEN_WIDTH, 1))

    for i, day in enumerate(data.forecast[:7]):
        cx = i * cell_w + cell_w // 2

        # Alternating column tint
        if i % 2 == 0:
            pygame.draw.rect(surface, config.CARD_COLOR,
                             (i * cell_w, y0, cell_w, h))

        day_surf = fonts["day"].render(day.day_name, True, config.TEXT_SECONDARY)
        _blit_c(surface, day_surf, cx, y0 + 8)

        draw_weather_icon(surface, cx, y0 + 55, 46, day.icon_type)

        hi_surf = fonts["day_temp"].render(f"{day.temp_max:.0f}°", True, config.HIGH_COLOR)
        lo_surf = fonts["day_temp"].render(f"{day.temp_min:.0f}°", True, config.LOW_COLOR)
        gap     = 5
        total_w = hi_surf.get_width() + gap + lo_surf.get_width()
        xs      = cx - total_w // 2
        ty      = y0 + h - hi_surf.get_height() - 20
        surface.blit(hi_surf, (xs, ty))
        surface.blit(lo_surf, (xs + hi_surf.get_width() + gap, ty))

    # "tap for hourly" hint at bottom-right of strip
    hint = fonts["small"].render("tap for hourly  ▸", True, config.TEXT_SECONDARY)
    _blit_r(surface, hint, config.SCREEN_WIDTH - 8, y0 + h - hint.get_height() - 5)


def _draw_stale(surface, fonts, data):
    if data is None:
        return
    elapsed = int((datetime.now() - data.fetched_at).total_seconds() / 60)
    if elapsed < 2:
        return
    surf = fonts["small"].render(f"updated {elapsed} min ago", True, config.TEXT_SECONDARY)
    _blit_r(surface, surf, config.SCREEN_WIDTH - 10,
            config.TOP_BAR_H + config.MAIN_H - surf.get_height() - 6)


def draw_main_page(surface, fonts, data):
    _draw_background(surface)
    _draw_top_bar(surface, fonts)
    _draw_main_content(surface, fonts, data)
    _draw_forecast_strip(surface, fonts, data)
    _draw_stale(surface, fonts, data)


# ---------------------------------------------------------------------------
# Hourly detail page
# ---------------------------------------------------------------------------

def _draw_hourly_header(surface, fonts):
    now = datetime.now()

    pygame.draw.rect(surface, config.CARD_COLOR,
                     (0, 0, config.SCREEN_WIDTH, config.TOP_BAR_H))
    pygame.draw.rect(surface, config.DIVIDER_COLOR,
                     (0, config.TOP_BAR_H - 1, config.SCREEN_WIDTH, 1))

    back_surf  = fonts["small"].render("← tap to return", True, config.ACCENT_COLOR)
    title_surf = fonts["condition"].render("Hourly Forecast", True, config.TEXT_PRIMARY)
    clock_surf = fonts["stat"].render(now.strftime("%H:%M"), True, config.TEXT_SECONDARY)

    cy = (config.TOP_BAR_H - back_surf.get_height()) // 2
    surface.blit(back_surf, (14, cy))
    _blit_c(surface, title_surf, config.SCREEN_WIDTH // 2,
            (config.TOP_BAR_H - title_surf.get_height()) // 2)
    _blit_r(surface, clock_surf, config.SCREEN_WIDTH - 14,
            (config.TOP_BAR_H - clock_surf.get_height()) // 2)


def _draw_hourly_cards(surface, fonts, data):
    if data is None or not data.hourly:
        surf = fonts["condition"].render("No hourly data available.",
                                        True, config.TEXT_SECONDARY)
        _blit_c(surface, surf, config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT // 2)
        return

    card_w = 160   # 5 × 160 = 800
    card_h = config.HOURLY_CARD_H

    for idx, hour in enumerate(data.hourly[:10]):
        row = idx // 5
        col = idx % 5
        cx  = col * card_w + card_w // 2
        y0  = config.TOP_BAR_H + row * card_h

        # Card tint
        tint = config.CARD_COLOR if idx % 2 == 0 else config.BG_COLOR
        pygame.draw.rect(surface, tint, (col * card_w, y0, card_w, card_h))

        # Hour label
        h_surf = fonts["small"].render(hour.hour_label, True, config.TEXT_SECONDARY)
        _blit_c(surface, h_surf, cx, y0 + 12)

        # Icon
        draw_weather_icon(surface, cx, y0 + 78, 76, hour.icon_type)

        # Temperature
        t_surf = fonts["card_temp"].render(f"{hour.temp:.0f}°C",
                                           True, config.TEXT_PRIMARY)
        _blit_c(surface, t_surf, cx, y0 + 126)

        # Precipitation %
        p_col  = config.RAIN_COLOR if hour.precip_prob > 0 else config.TEXT_SECONDARY
        p_surf = fonts["small"].render(f"{hour.precip_prob}%", True, p_col)
        _blit_c(surface, p_surf, cx, y0 + 156)

    # Row separator
    sep_y = config.TOP_BAR_H + card_h
    pygame.draw.rect(surface, config.DIVIDER_COLOR,
                     (0, sep_y, config.SCREEN_WIDTH, 1))


def _draw_hourly_footer(surface, fonts):
    footer_y = config.TOP_BAR_H + 2 * config.HOURLY_CARD_H
    pygame.draw.rect(surface, config.CARD_COLOR,
                     (0, footer_y, config.SCREEN_WIDTH,
                      config.SCREEN_HEIGHT - footer_y))
    pygame.draw.rect(surface, config.DIVIDER_COLOR,
                     (0, footer_y, config.SCREEN_WIDTH, 1))
    surf = fonts["small"].render("tap anywhere to return",
                                 True, config.TEXT_SECONDARY)
    _blit_c(surface, surf, config.SCREEN_WIDTH // 2,
            footer_y + (config.SCREEN_HEIGHT - footer_y - surf.get_height()) // 2)


def draw_hourly_page(surface, fonts, data):
    _draw_background(surface)
    _draw_hourly_header(surface, fonts)
    _draw_hourly_cards(surface, fonts, data)
    _draw_hourly_footer(surface, fonts)


# ---------------------------------------------------------------------------
# Master entry point
# ---------------------------------------------------------------------------

def draw_all(surface, fonts, data, state, page="main"):
    if page == "hourly":
        draw_hourly_page(surface, fonts, data)
    else:
        draw_main_page(surface, fonts, data)

    if state == "loading":
        _draw_loading(surface, fonts)
    elif state == "error":
        _draw_error(surface, fonts)
