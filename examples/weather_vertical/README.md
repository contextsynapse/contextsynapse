# Weather Vertical — ContextCore Example Plugin

This is a complete example showing how to build a vertical application on ContextCore.

## What it demonstrates

- **Schema**: Custom node types (WeatherReading, WeatherAlert, City)
- **Sensor**: Scheduled data collection (simulated weather readings)
- **API**: Custom FastAPI routes (/weather/cities, /weather/current/{city})
- **MCP Tool**: Claude/Copilot integration (weather_lookup)
- **Dashboard Card**: UI component injection

## Install

```bash
cd examples/weather_vertical
pip install -e .
```

## Usage

Start the ContextCore server — the plugin auto-discovers:

```bash
python -m uvicorn contextsynapse.api.api:app --reload
```

Check that it loaded:
```bash
curl http://localhost:8000/plugins/
```

Query weather data:
```
FIND NODES WHERE type = 'WeatherReading' AND city = 'Tokyo'
```

## Build Your Own

Copy this example and modify:

1. Define your schemas in `PluginSchema`
2. Implement sensors that `collect()` data from your domain
3. Add API routes for domain-specific queries
4. Register via `pyproject.toml` entry points

See the [Plugin Development Guide](../../docs/plugins.md) for full documentation.
