"""
Weather Station — entry point.

Pi framebuffer (no desktop):
    SDL_VIDEODRIVER=fbcon SDL_FBDEV=/dev/fb1 python main.py

Desktop (windowed, for dev/testing):
    python main.py --windowed
"""

import sys
import threading
from datetime import datetime

import pygame

import config
import renderer
import weather

# ---------------------------------------------------------------------------
# Shared state between fetch thread and render loop
# ---------------------------------------------------------------------------
_shared = {
    "data":  None,
    "state": "loading",   # "loading" | "ok" | "error"
    "lock":  threading.Lock(),
}


# ---------------------------------------------------------------------------
# Background fetch thread
# ---------------------------------------------------------------------------

def _fetch_loop(stop_event, force_refresh_event):
    while not stop_event.is_set():
        force_refresh_event.clear()
        raw = weather.fetch_weather(config.LATITUDE, config.LONGITUDE)
        with _shared["lock"]:
            if raw is not None:
                _shared["data"]  = weather.parse_weather(raw)
                _shared["state"] = "ok"
            else:
                if _shared["data"] is None:
                    _shared["state"] = "error"
        force_refresh_event.wait(config.REFRESH_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    windowed = "--windowed" in sys.argv

    pygame.init()
    pygame.display.set_caption("Weather Station")

    if windowed:
        screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    else:
        screen = pygame.display.set_mode(
            (config.SCREEN_WIDTH, config.SCREEN_HEIGHT),
            pygame.FULLSCREEN | pygame.NOFRAME,
        )
        pygame.mouse.set_visible(False)

    fonts = renderer.load_fonts()
    clock = pygame.time.Clock()

    stop_event          = threading.Event()
    force_refresh_event = threading.Event()

    fetch_thread = threading.Thread(
        target=_fetch_loop,
        args=(stop_event, force_refresh_event),
        daemon=True,
        name="fetch",
    )
    fetch_thread.start()

    page             = "main"   # "main" | "hourly"
    last_touch       = datetime.min
    TOUCH_DEBOUNCE_S = 2.0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    force_refresh_event.set()
                elif event.key == pygame.K_h:
                    page = "hourly" if page == "main" else "main"

            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                now   = datetime.now()
                delta = (now - last_touch).total_seconds()
                if delta >= TOUCH_DEBOUNCE_S:
                    last_touch = now
                    if page == "hourly":
                        page = "main"
                    else:
                        # Tap forecast strip → hourly page; tap main area → refresh
                        touch_y = (event.pos[1] if hasattr(event, "pos")
                                   else int(event.y * config.SCREEN_HEIGHT))
                        if touch_y >= config.FORECAST_Y:
                            page = "hourly"
                        else:
                            force_refresh_event.set()

        with _shared["lock"]:
            data  = _shared["data"]
            state = _shared["state"]

        renderer.draw_all(screen, fonts, data, state, page)
        pygame.display.flip()
        clock.tick(config.FPS)

    stop_event.set()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
