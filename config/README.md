# AIContextDB Configuration Directory

This directory contains all user-editable configuration files for AIContextDB.

## Directory Structure

```
config/
├── core/                      # Core system configuration (required)
│   ├── database.yaml          # Database, performance, storage settings
│   ├── application.yaml       # Application-level settings (LLM, API, search)
│   ├── namespace.yaml          # Default namespace configuration
│   ├── columnar.yaml          # Columnar storage configuration
│   ├── graph.yaml             # Graph configuration
│   └── metadata_schema.yaml   # Metadata schema definition
│
├── pipelines/                  # Pipeline configurations (optional)
│   ├── enterprise.yaml        # Enterprise document analysis pipeline
│   ├── enterprise_enhanced.yaml
│   ├── research.yaml          # Research document pipeline
│   ├── research_enhanced.yaml
│   ├── multimedia.yaml        # Multimedia content pipeline
│   └── multimedia_enhanced.yaml
│
├── strategies/                 # Strategy configurations (optional)
│   ├── chunking.yaml          # Chunking strategies
│   ├── custom.yaml            # Custom strategies
│   └── custom_passes.yaml     # Custom processing passes
│
├── infrastructure/             # Infrastructure configurations (optional)
│   └── ray.yaml               # Ray cluster configuration
│
├── examples/                   # Example configurations (reference only)
│   └── document_store.yaml    # Example document store configuration
│
└── environments/               # Environment-specific overrides (optional)
    └── production.yaml        # Production environment overrides
```

## Configuration Files

### Core Configuration

#### `core/database.yaml`
Main database configuration file. Contains:
- Performance settings (cache, indexing, WAL)
- Storage configuration
- Graph engine settings
- Traceability settings

**Note**: This replaces the legacy `contextcore.conf` file.

#### `core/application.yaml`
Application-level settings:
- Search configuration (hybrid search, weights)
- Embedding settings (provider, model, dimensions)
- LLM configuration (provider, model, API keys)
- API server settings (host, port, CORS, rate limits)

**Note**: This replaces the legacy `config/config.yaml` file.

### Pipeline Configurations

Pipeline configs define how documents are processed:
- **enterprise.yaml**: Enterprise document analysis
- **research.yaml**: Research document processing
- **multimedia.yaml**: Multimedia content processing

### Strategy Configurations

Strategy configs define processing strategies:
- **chunking.yaml**: Text chunking strategies
- **custom.yaml**: Custom user-defined strategies
- **custom_passes.yaml**: Custom processing passes

### Infrastructure Configurations

Infrastructure configs for distributed computing:
- **ray.yaml**: Ray cluster configuration for scaling

### Environment Configurations

Environment-specific overrides:
- **production.yaml**: Production environment settings

## Loading Order

Configuration files are loaded in this order (later files override earlier ones):

1. `core/database.yaml` (base database config)
2. `core/application.yaml` (base application config)
3. Environment-specific config (if specified)
4. Namespace-specific config (if exists)

## Migration from Legacy Structure

If you have existing configuration files, they have been automatically migrated:

| Old Location | New Location |
|------------|-------------|
| `contextcore.conf` | `core/database.yaml` |
| `config/config.yaml` | `core/application.yaml` |
| `config/ray_config.yaml` | `infrastructure/ray.yaml` |
| `config/enterprise_pipeline_config.yaml` | `pipelines/enterprise.yaml` |
| `config/chunking_strategies.yaml` | `strategies/chunking.yaml` |

## Backward Compatibility

The codebase maintains backward compatibility with legacy paths:
- `contextcore.conf` → `config/core/database.yaml`
- `config/config.yaml` → `config/core/application.yaml`
- `config/ray_config.yaml` → `config/infrastructure/ray.yaml`

However, it's recommended to use the new structure going forward.

## Configuration Code vs Data

**Important**: This directory contains configuration DATA (YAML/JSON files).

Configuration CODE (Python classes and loaders) is located in:
- `contextcore/config/` - Python modules with configuration classes

## Examples

See `examples/` directory for example configurations that you can copy and modify.

## Documentation

For more information on specific configuration options, see:
- Database config: See `core/database.yaml` comments
- Application config: See `core/application.yaml` comments
- Pipeline configs: See pipeline documentation
- Strategy configs: See strategy documentation
































