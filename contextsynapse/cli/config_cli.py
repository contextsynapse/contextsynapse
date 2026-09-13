#!/usr/bin/env python3
"""
Configuration CLI Tool

Manage AIContextDB configuration files, similar to:
- PostgreSQL: psql -c "SHOW config_file"
- MySQL: mysqladmin variables
- MongoDB: mongosh admin --eval "db.getSiblingDB('admin').runCommand({getParameter: 1})"
"""

import click
import sys
from pathlib import Path
from typing import Optional
from ..config.database_config import (
    DatabaseConfigManager, 
    DatabaseConfig,
    ConfigFormat,
    get_config_manager
)


@click.group()
def config():
    """AIContextDB Configuration Management"""
    pass


@config.command()
@click.option('--file', '-f', help='Configuration file path')
@click.option('--format', type=click.Choice(['yaml', 'json']), default='yaml', help='Output format')
def show(file: Optional[str], format: str):
    """Show current configuration"""
    manager = get_config_manager(file)
    config = manager.get_config()
    
    if format == 'yaml':
        import yaml
        config_dict = manager._config_to_dict(config)
        print(yaml.dump(config_dict, default_flow_style=False, sort_keys=False))
    else:
        import json
        config_dict = manager._config_to_dict(config)
        print(json.dumps(config_dict, indent=2))


@config.command()
@click.option('--file', '-f', help='Output file path')
@click.option('--format', type=click.Choice(['yaml', 'json']), default='yaml', help='File format')
def init(file: Optional[str], format: str):
    """Create default configuration file"""
    if file:
        manager = DatabaseConfigManager(file)
    else:
        manager = get_config_manager()
    
    manager.create_default_config()
    print(f"[EMOJI] Default configuration created at: {manager.config_file}")
    print(f"   Edit this file to customize your settings")


@config.command()
@click.argument('section')
@click.argument('key')
@click.argument('value')
@click.option('--file', '-f', help='Configuration file path')
def set(section: str, key: str, value: str, file: Optional[str]):
    """Set a configuration value"""
    manager = get_config_manager(file)
    
    # Parse value (try int, float, bool, then string)
    try:
        if value.lower() in ('true', 'yes', 'on'):
            value = True
        elif value.lower() in ('false', 'no', 'off'):
            value = False
        elif '.' in value:
            value = float(value)
        else:
            value = int(value)
    except ValueError:
        pass  # Keep as string
    
    manager.update_config(section, **{key: value})
    manager.save_config()
    print(f"[EMOJI] Set {section}.{key} = {value}")


@config.command()
@click.argument('section')
@click.argument('key')
@click.option('--file', '-f', help='Configuration file path')
def get(section: str, key: str, file: Optional[str]):
    """Get a configuration value"""
    manager = get_config_manager(file)
    config = manager.get_config()
    
    if not hasattr(config, section):
        print(f"[EMOJI] Unknown section: {section}", file=sys.stderr)
        sys.exit(1)
    
    section_obj = getattr(config, section)
    if not hasattr(section_obj, key):
        print(f"[EMOJI] Unknown key: {section}.{key}", file=sys.stderr)
        sys.exit(1)
    
    value = getattr(section_obj, key)
    print(value)


@config.command()
@click.option('--file', '-f', help='Configuration file path')
def validate(file: Optional[str]):
    """Validate configuration file"""
    manager = get_config_manager(file)
    config = manager.get_config()
    
    errors = []
    warnings = []
    
    # Validate performance settings
    if config.performance.max_nodes_in_memory < 1000:
        warnings.append("max_nodes_in_memory is very low (< 1000)")
    
    if config.performance.cache_size_mb < 64:
        warnings.append("cache_size_mb is very low (< 64 MB)")
    
    # Validate storage settings
    if config.storage.storage_format not in ['multi_file', 'single_file']:
        errors.append(f"Invalid storage_format: {config.storage.storage_format}")
    
    # Validate connection settings
    if config.connection.api_port < 1024:
        warnings.append("API port < 1024 may require root privileges")
    
    if config.connection.api_port > 65535:
        errors.append(f"Invalid API port: {config.connection.api_port}")
    
    # Validate logging
    if config.logging.log_level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
        errors.append(f"Invalid log_level: {config.logging.log_level}")
    
    # Print results
    if errors:
        print("[EMOJI] Configuration Errors:")
        for error in errors:
            print(f"   - {error}")
    
    if warnings:
        print("[EMOJI][EMOJI]  Configuration Warnings:")
        for warning in warnings:
            print(f"   - {warning}")
    
    if not errors and not warnings:
        print("[EMOJI] Configuration is valid")
    
    sys.exit(1 if errors else 0)


@config.command()
@click.option('--file', '-f', help='Configuration file path')
def location(file: Optional[str]):
    """Show configuration file location"""
    manager = get_config_manager(file)
    print(f"Configuration file: {manager.config_file}")
    print(f"Exists: {manager.config_file.exists()}")


if __name__ == '__main__':
    config()







