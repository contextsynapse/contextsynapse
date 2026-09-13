"""
Performance Monitoring and Resource Tracking for Pipeline Execution

This module provides comprehensive performance monitoring including:
- Memory usage tracking
- CPU usage tracking
- Disk I/O monitoring
- Batch processing metrics
- Performance warnings and alerts
"""
import time
import sys
import gc
from typing import Dict, Any, Optional, List
from datetime import datetime
import json


class PerformanceMonitor:
    """Monitor system performance during pipeline execution."""
    
    def __init__(self, namespace: str = "default", enable_detailed_logging: bool = True):
        self.namespace = namespace
        self.enable_detailed_logging = enable_detailed_logging
        self.metrics = []
        self.start_time = None
        self.psutil_available = False
        
        try:
            import psutil
            self.psutil = psutil
            self.psutil_available = True
            self.process = psutil.Process()
        except ImportError:
            print("[PERF] psutil not available, limited monitoring", flush=True)
    
    def start_monitoring(self, stage_name: str):
        """Start monitoring a stage."""
        self.start_time = time.time()
        initial_metrics = self._collect_metrics()
        initial_metrics['stage'] = stage_name
        initial_metrics['event'] = 'start'
        initial_metrics['timestamp'] = datetime.now().isoformat()
        self.metrics.append(initial_metrics)
        
        if self.enable_detailed_logging:
            print(f"\n[PERF] {'='*70}", flush=True)
            print(f"[PERF] Starting monitoring: {stage_name}", flush=True)
            print(f"[PERF] {'='*70}", flush=True)
            self._print_metrics(initial_metrics)
    
    def check_memory(self) -> Optional[float]:
        """Get current memory usage in MB."""
        if not self.psutil_available:
            return None
        try:
            return self.process.memory_info().rss / (1024 * 1024)  # MB
        except:
            return None
    
    def check_cpu(self) -> Optional[float]:
        """Get current CPU usage percentage."""
        if not self.psutil_available:
            return None
        try:
            return self.process.cpu_percent(interval=0.1)
        except:
            return None
    
    def check_disk_io(self) -> Optional[Dict[str, Any]]:
        """Get disk I/O statistics."""
        if not self.psutil_available:
            return None
        try:
            io_counters = self.process.io_counters()
            return {
                'read_count': io_counters.read_count,
                'write_count': io_counters.write_count,
                'read_bytes': io_counters.read_bytes / (1024 * 1024),  # MB
                'write_bytes': io_counters.write_bytes / (1024 * 1024)  # MB
            }
        except:
            return None
    
    def _collect_metrics(self) -> Dict[str, Any]:
        """Collect all available metrics."""
        metrics = {
            'memory_mb': self.check_memory(),
            'cpu_percent': self.check_cpu(),
            'disk_io': self.check_disk_io(),
            'gc_counts': dict(zip(['gen0', 'gen1', 'gen2'], gc.get_count())),
        }
        return metrics
    
    def _print_metrics(self, metrics: Dict[str, Any]):
        """Print metrics in a readable format."""
        if metrics.get('memory_mb'):
            print(f"[PERF] Memory: {metrics['memory_mb']:.1f} MB", flush=True)
        if metrics.get('cpu_percent'):
            print(f"[PERF] CPU: {metrics['cpu_percent']:.1f}%", flush=True)
        if metrics.get('disk_io'):
            io = metrics['disk_io']
            print(f"[PERF] Disk I/O: Read {io['read_bytes']:.1f}MB, Write {io['write_bytes']:.1f}MB", flush=True)
        print(f"[PERF] GC: {metrics.get('gc_counts', {})}", flush=True)
    
    def log_checkpoint(self, checkpoint_name: str, items_processed: int = 0, 
                      additional_info: Optional[Dict[str, Any]] = None):
        """Log a performance checkpoint."""
        if self.start_time is None:
            return
        
        elapsed = time.time() - self.start_time
        current_metrics = self._collect_metrics()
        
        checkpoint = {
            'checkpoint': checkpoint_name,
            'elapsed_seconds': elapsed,
            'items_processed': items_processed,
            'items_per_second': items_processed / elapsed if elapsed > 0 else 0,
            **current_metrics,
            'timestamp': datetime.now().isoformat()
        }
        
        if additional_info:
            checkpoint.update(additional_info)
        
        self.metrics.append(checkpoint)
        
        if self.enable_detailed_logging:
            print(f"\n[PERF] Checkpoint: {checkpoint_name}", flush=True)
            print(f"[PERF] Elapsed: {elapsed:.2f}s, Items: {items_processed}, Rate: {checkpoint['items_per_second']:.1f}/s", flush=True)
            self._print_metrics(current_metrics)
            
            # Memory warnings
            if current_metrics.get('memory_mb'):
                if current_metrics['memory_mb'] > 8000:  # 8GB
                    print(f"[PERF] ⚠️  WARNING: High memory usage: {current_metrics['memory_mb']:.1f}MB", flush=True)
                elif current_metrics['memory_mb'] > 4000:  # 4GB
                    print(f"[PERF] ⚠️  Memory usage: {current_metrics['memory_mb']:.1f}MB", flush=True)
    
    def end_monitoring(self, stage_name: str, summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """End monitoring and return summary."""
        if self.start_time is None:
            return {}
        
        total_time = time.time() - self.start_time
        final_metrics = self._collect_metrics()
        
        # Calculate deltas
        if len(self.metrics) > 0:
            initial_metrics = self.metrics[0]
            memory_delta = None
            if initial_metrics.get('memory_mb') and final_metrics.get('memory_mb'):
                memory_delta = final_metrics['memory_mb'] - initial_metrics['memory_mb']
        else:
            memory_delta = None
        
        summary_data = {
            'stage': stage_name,
            'total_time_seconds': total_time,
            'total_time_minutes': total_time / 60,
            'initial_metrics': self.metrics[0] if self.metrics else {},
            'final_metrics': final_metrics,
            'memory_delta_mb': memory_delta,
            'checkpoints': len([m for m in self.metrics if 'checkpoint' in m]),
            'timestamp': datetime.now().isoformat()
        }
        
        if summary:
            summary_data.update(summary)
        
        self.metrics.append({
            'event': 'end',
            **summary_data
        })
        
        if self.enable_detailed_logging:
            print(f"\n[PERF] {'='*70}", flush=True)
            print(f"[PERF] Monitoring Summary: {stage_name}", flush=True)
            print(f"[PERF] {'='*70}", flush=True)
            print(f"[PERF] Total Time: {total_time:.2f}s ({total_time/60:.2f} minutes)", flush=True)
            if memory_delta:
                print(f"[PERF] Memory Delta: {memory_delta:+.1f} MB", flush=True)
            self._print_metrics(final_metrics)
            print(f"[PERF] {'='*70}\n", flush=True)
        
        return summary_data
    
    def get_metrics_history(self) -> List[Dict[str, Any]]:
        """Get all collected metrics."""
        return self.metrics
    
    def save_metrics(self, filepath: str):
        """Save metrics to JSON file."""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump({
                    'namespace': self.namespace,
                    'metrics': self.metrics
                }, f, indent=2, default=str)
            print(f"[PERF] Metrics saved to {filepath}", flush=True)
        except Exception as e:
            print(f"[PERF] Failed to save metrics: {e}", flush=True)


class BatchProcessor:
    """Optimized batch processing with performance monitoring."""
    
    def __init__(self, batch_size: int = 100, flush_interval: int = 10, 
                 monitor: Optional[PerformanceMonitor] = None):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.monitor = monitor
        self.items_processed = 0
        self.batches_processed = 0
        self.start_time = time.time()
    
    def process_batch(self, items: List[Any], process_fn, flush_fn=None, 
                     batch_idx: int = 0) -> int:
        """Process a batch of items with monitoring."""
        batch_start = time.time()
        processed = 0
        
        try:
            for item in items:
                process_fn(item)
                processed += 1
                self.items_processed += 1
            
            batch_time = time.time() - batch_start
            self.batches_processed += 1
            
            # Periodic flush
            if flush_fn and (batch_idx + 1) % self.flush_interval == 0:
                flush_start = time.time()
                flush_fn()
                flush_time = time.time() - flush_start
                
                if self.monitor:
                    self.monitor.log_checkpoint(
                        f"Batch {batch_idx + 1}",
                        items_processed=self.items_processed,
                        additional_info={
                            'batch_time': batch_time,
                            'flush_time': flush_time,
                            'items_per_second': processed / batch_time if batch_time > 0 else 0
                        }
                    )
                else:
                    print(f"[BATCH] Processed batch {batch_idx + 1}: {processed} items in {batch_time:.2f}s, flush: {flush_time:.2f}s", flush=True)
            
            return processed
            
        except MemoryError:
            # Force garbage collection and flush
            gc.collect()
            if flush_fn:
                flush_fn()
            raise
        except Exception as e:
            print(f"[BATCH] Error processing batch {batch_idx + 1}: {e}", flush=True)
            raise
    
    def get_summary(self) -> Dict[str, Any]:
        """Get processing summary."""
        total_time = time.time() - self.start_time
        return {
            'items_processed': self.items_processed,
            'batches_processed': self.batches_processed,
            'total_time_seconds': total_time,
            'items_per_second': self.items_processed / total_time if total_time > 0 else 0
        }

















