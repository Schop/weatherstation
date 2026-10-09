import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime


@dataclass
class HourlyForecast:
    hour_label: str   # "14:00"
    weather_code: int
    icon_type: str
    temp: float
    precip_prob: int  # 0-100


@dataclass
class DayForecast:
    day_name: str
    weather_code: int
    icon_type: str
    condition: str
    temp_max: float
    temp_min: float
    precip_prob: int


@dataclass
class WeatherData:
    temp: float
    feels_like: float
    humidity: int
    wind_speed: float
    wind_dir_label: str
    weather_code: int
    icon_type: str
    condition: str
    uv_index: float
    is_day: bool
    sunrise: str
    sunset: str
    precip_prob: int
    hourly: list
    forecast: list
    fetched_at: datetime


_WMO_TABLE = [
    ({0},              "Helder",               "clear"),
    ({1},              "Overwegend helder",     "clear"),
    ({2},              "Gedeeltelijk bewolkt",  "partly_cloudy"),
    ({3},              "Bewolkt",               "cloudy"),
    ({45, 48},         "Mist",                 "fog"),
    ({51, 53, 55},     "Motregen",             "drizzle"),
    ({56, 57},         "Bevriezende motregen", "drizzle"),
    ({61, 63, 65},     "Regen",                "rain"),
    ({66, 67},         "IJsregen",             "sleet"),
    ({71, 73, 75},     "Sneeuwval",            "snow"),
    ({77},             "Sneeuwkorrels",        "snow"),
    ({80, 81, 82},     "Regenbuien",           "rain"),
    ({85, 86},         "Sneeuwbuien",          "snow"),
    ({95},             "Onweer",               "storm"),
    ({96, 99},         "Onweer met hagel",     "storm"),
]

_COMPASS = (
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW",
)


def wmo_to_info(code, is_day=True):
    for codes, desc, icon in _WMO_TABLE:
        if code in codes:
            if icon == "clear":
                icon = "clear_day" if is_day else "clear_night"
            return desc, icon
    return "Onbekend", "cloudy"


def wind_direction_label(degrees):
    return _COMPASS[round(degrees / 22.5) % 16]


def _parse_hhmm(iso_str):
    return iso_str[11:16] if iso_str and len(iso_str) >= 16 else "--:--"


def build_url(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m", "relative_humidity_2m",
            "apparent_temperature", "weather_code",
            "wind_speed_10m", "wind_direction_10m",
            "uv_index", "is_day",
        ]),
        "hourly": ",".join([
            "temperature_2m", "precipitation_probability",
            "weather_code", "is_day",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max",
            "temperature_2m_min", "sunrise", "sunset",
            "precipitation_probability_max",
        ]),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
        "forecast_days": 7,
    }
    return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)


def fetch_weather(lat, lon):
    if lat is None or lon is None:        # location not configured (see config_local.py)
        return None
    url = build_url(lat, lon)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"[weather] fetch error: {exc}", file=sys.stderr)
        return None


def parse_weather(raw):
    c = raw.get("current", {})
    d = raw.get("daily",   {})
    h = raw.get("hourly",  {})

    is_day = bool(c.get("is_day", 1))
    code = c.get("weather_code", 0)
    condition, icon_type = wmo_to_info(code, is_day)

    # Locate current hour in hourly time series
    now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
    h_times = h.get("time", [])
    try:
        hour_idx = h_times.index(now_str)
    except ValueError:
        hour_idx = 0

    h_temps   = h.get("temperature_2m", [])
    h_precip  = h.get("precipitation_probability", [])
    h_codes   = h.get("weather_code", [])
    h_is_day  = h.get("is_day", [])

    precip_prob = int(h_precip[hour_idx]) if hour_idx < len(h_precip) else 0

    hourly = []
    for i in range(hour_idx, min(hour_idx + 12, len(h_times))):
        h_code = h_codes[i]  if i < len(h_codes)  else 0
        h_day  = bool(h_is_day[i]) if i < len(h_is_day) else True
        _, h_icon = wmo_to_info(h_code, h_day)
        hourly.append(HourlyForecast(
            hour_label=h_times[i][11:16],
            weather_code=h_code,
            icon_type=h_icon,
            temp=h_temps[i]  if i < len(h_temps)  else 0.0,
            precip_prob=int(h_precip[i]) if i < len(h_precip) else 0,
        ))

    forecast = []
    d_codes  = d.get("weather_code", [])
    d_maxes  = d.get("temperature_2m_max", [])
    d_mins   = d.get("temperature_2m_min", [])
    d_times  = d.get("time", [])
    d_precip = d.get("precipitation_probability_max", [])

    for i in range(min(7, len(d_codes))):
        day_code = d_codes[i]
        day_cond, day_icon = wmo_to_info(day_code, is_day=True)
        try:
            day_name = datetime.strptime(d_times[i], "%Y-%m-%d").strftime("%A")
        except Exception:
            day_name = "---"
        forecast.append(DayForecast(
            day_name=day_name,
            weather_code=day_code,
            icon_type=day_icon,
            condition=day_cond,
            temp_max=d_maxes[i]  if i < len(d_maxes)  else 0.0,
            temp_min=d_mins[i]   if i < len(d_mins)   else 0.0,
            precip_prob=int(d_precip[i]) if i < len(d_precip) else 0,
        ))

    sunrises = d.get("sunrise", [])
    sunsets  = d.get("sunset",  [])

    return WeatherData(
        temp=c.get("temperature_2m", 0.0),
        feels_like=c.get("apparent_temperature", 0.0),
        humidity=int(c.get("relative_humidity_2m", 0)),
        wind_speed=c.get("wind_speed_10m", 0.0),
        wind_dir_label=wind_direction_label(c.get("wind_direction_10m", 0)),
        weather_code=code,
        icon_type=icon_type,
        condition=condition,
        uv_index=c.get("uv_index", 0.0),
        is_day=is_day,
        sunrise=_parse_hhmm(sunrises[0]) if sunrises else "--:--",
        sunset =_parse_hhmm(sunsets[0])  if sunsets  else "--:--",
        precip_prob=precip_prob,
        hourly=hourly,
        forecast=forecast,
        fetched_at=datetime.now(),
    )


if __name__ == "__main__":
    import config
    raw = fetch_weather(config.LATITUDE, config.LONGITUDE)
    if raw:
        data = parse_weather(raw)
        print(f"Current: {data.temp:.0f}°C, {data.condition}, precip {data.precip_prob}%")
        print(f"Hourly ({len(data.hourly)} entries):")
        for h in data.hourly:
            print(f"  {h.hour_label}  {h.temp:.0f}°C  {h.precip_prob}%  {h.icon_type}")
    else:
        print("Fetch failed.")
