"""
AIQL Planner - Creates optimized execution plans with dependency analysis and parallelization

The planner analyzes validated AST and creates an optimized execution plan with:
- Dependency analysis (what depends on what?)
- Cost estimation (how expensive is each operation?)
- Parallel vs sequential optimization (what can run in parallel?)
- Execution stages (how to execute efficiently?)
"""

from typing import Dict, List, Any, Optional, Set, Tuple
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict

class ExecutionMode(str, Enum):
    """Execution modes"""
    SEQUENTIAL = "sequential"  # One operation at a time
    PARALLEL = "parallel"      # Multiple operations simultaneously
    HYBRID = "hybrid"          # Mix of sequential and parallel

class OperationType(str, Enum):
    """Operation types"""
    LOAD = "load"
    EXTRACT = "extract"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    ENTITY_EXTRACT = "entity_extract"
    RELATIONSHIP_EXTRACT = "relationship_extract"
    CONNECT = "connect"
    QUERY = "query"  # For RAG queries
    CREATE_GRAPH = "create_graph"
    UPDATE = "update"

@dataclass
class Operation:
    """Represents a single operation in the execution plan"""
    op_id: str
    op_type: OperationType
    dependencies: Set[str] = field(default_factory=set)
    estimated_cost: int = 1  # Cost units (1-10 scale)
    estimated_duration: float = 0.0  # Estimated duration in seconds
    can_parallelize: bool = True
    data_volume: int = 0  # Estimated data volume
    resource_intensive: bool = False
    
@dataclass
class ExecutionStage:
    """Represents a stage in the execution plan"""
    stage_id: str
    operations: List[Operation]
    execution_mode: ExecutionMode
    max_parallel_ops: int = 4
    estimated_duration: float = 0.0

@dataclass
class ExecutionPlan:
    """Complete execution plan"""
    stages: List[ExecutionStage]
    total_estimated_duration: float = 0.0
    can_parallelize: bool = False
    dependency_graph: Dict[str, Set[str]] = field(default_factory=dict)
    
class AIQLPlanner:
    """
    Creates optimized execution plans for AIQL queries
    """
    
    def __init__(self, max_parallel_ops: int = 4):
        """
        Initialize planner
        
        Args:
            max_parallel_ops: Maximum number of operations to run in parallel
        """
        self.max_parallel_ops = max_parallel_ops
        self.operations = {}  # op_id -> Operation
        self.dependency_graph = defaultdict(set)  # op_id -> {dependencies}
    
    def create_plan(self, validated_ast: Dict[str, Any]) -> ExecutionPlan:
        """
        Create optimized execution plan from validated AST
        
        Args:
            validated_ast: Validated AST from validator
            
        Returns:
            Optimized execution plan
        """
        # Reset
        self.operations = {}
        self.dependency_graph = defaultdict(set)
        
        # Extract operations from AST
        operations = self._extract_operations(validated_ast)
        
        # Build dependency graph
        dependency_graph = self._build_dependency_graph(operations, validated_ast)
        
        # Estimate costs
        self._estimate_costs(operations)
        
        # Create execution stages
        stages = self._create_execution_stages(operations, dependency_graph)
        
        # Calculate total duration
        total_duration = sum(stage.estimated_duration for stage in stages)
        
        return ExecutionPlan(
            stages=stages,
            total_estimated_duration=total_duration,
            can_parallelize=any(stage.execution_mode == ExecutionMode.PARALLEL for stage in stages),
            dependency_graph=dependency_graph
        )
    
    def _extract_operations(self, ast: Dict[str, Any]) -> List[Operation]:
        """Extract operations from AST"""
        operations = []
        query_type = ast.get('query_type', 'UNKNOWN')
        
        if query_type == 'PIPELINE':
            stages = ast.get('stages', [])
            for i, stage in enumerate(stages):
                op = self._create_operation_from_stage(stage, i)
                operations.append(op)
        
        elif query_type == 'RAG':
            # RAG queries are single operations
            op = Operation(
                op_id="rag_query_1",
                op_type=OperationType.QUERY,
                estimated_cost=5,
                estimated_duration=2.0,
                can_parallelize=False
            )
            operations.append(op)
        
        elif query_type == 'RETRIEVE':
            op = Operation(
                op_id="retrieve_1",
                op_type=OperationType.QUERY,
                estimated_cost=3,
                estimated_duration=1.5,
                can_parallelize=False
            )
            operations.append(op)
        
        elif query_type == 'HYBRID':
            # Hybrid queries combine RAG + RETRIEVE
            op1 = Operation(
                op_id="hybrid_1",
                op_type=OperationType.QUERY,
                estimated_cost=6,
                estimated_duration=3.0,
                can_parallelize=False
            )
            operations.append(op1)
        
        return operations
    
    def _create_operation_from_stage(self, stage: Dict[str, Any], index: int) -> Operation:
        """Create operation from pipeline stage"""
        stage_type = stage.get('type', '').upper()
        
        # Map stage type to operation type
        op_type_map = {
            'LOAD': OperationType.LOAD,
            'EXTRACT': OperationType.EXTRACT,
            'CHUNK': OperationType.CHUNK,
            'EMBED': OperationType.EMBED,
            'INDEX': OperationType.INDEX,
            'ENTITY_EXTRACT': OperationType.ENTITY_EXTRACT,
            'RELATIONSHIP_EXTRACT': OperationType.RELATIONSHIP_EXTRACT,
            'CONNECT': OperationType.CONNECT,
        }
        
        op_type = op_type_map.get(stage_type, OperationType.LOAD)
        
        # Estimate costs based on operation type
        cost_map = {
            OperationType.LOAD: 1,
            OperationType.EXTRACT: 2,
            OperationType.CHUNK: 3,
            OperationType.EMBED: 8,
            OperationType.ENTITY_EXTRACT: 7,
            OperationType.RELATIONSHIP_EXTRACT: 7,
            OperationType.INDEX: 4,
            OperationType.CONNECT: 3,
        }
        
        # Estimate duration (in seconds)
        duration_map = {
            OperationType.LOAD: 1.0,
            OperationType.EXTRACT: 2.0,
            OperationType.CHUNK: 3.0,
            OperationType.EMBED: 5.0,
            OperationType.ENTITY_EXTRACT: 4.0,
            OperationType.RELATIONSHIP_EXTRACT: 4.0,
            OperationType.INDEX: 2.0,
            OperationType.CONNECT: 1.0,
        }
        
        return Operation(
            op_id=f"{stage_type.lower()}_{index+1}",
            op_type=op_type,
            estimated_cost=cost_map.get(op_type, 1),
            estimated_duration=duration_map.get(op_type, 1.0),
            can_parallelize=self._can_parallelize(op_type),
            resource_intensive=self._is_resource_intensive(op_type)
        )
    
    def _can_parallelize(self, op_type: OperationType) -> bool:
        """Check if operation can be parallelized"""
        # These operations typically can't be parallelized due to dependencies
        non_parallel_ops = {
            OperationType.CHUNK,
            OperationType.EMBED,
            OperationType.INDEX,
            OperationType.CONNECT
        }
        return op_type not in non_parallel_ops
    
    def _is_resource_intensive(self, op_type: OperationType) -> bool:
        """Check if operation is resource-intensive"""
        resource_intensive_ops = {
            OperationType.EMBED,
            OperationType.ENTITY_EXTRACT,
            OperationType.RELATIONSHIP_EXTRACT
        }
        return op_type in resource_intensive_ops
    
    def _build_dependency_graph(self, operations: List[Operation], ast: Dict[str, Any]) -> Dict[str, Set[str]]:
        """Build dependency graph between operations"""
        dependency_graph = {}
        
        for i, op in enumerate(operations):
            deps = set()
            
            # EMBED depends on CHUNK
            if op.op_type == OperationType.EMBED:
                chunk_ops = [o for o in operations[:i] if o.op_type == OperationType.CHUNK]
                if chunk_ops:
                    deps.add(chunk_ops[-1].op_id)
            
            # INDEX depends on EMBED
            if op.op_type == OperationType.INDEX:
                embed_ops = [o for o in operations[:i] if o.op_type == OperationType.EMBED]
                if embed_ops:
                    deps.add(embed_ops[-1].op_id)
            
            # EXTRACT depends on LOAD
            if op.op_type == OperationType.EXTRACT:
                load_ops = [o for o in operations[:i] if o.op_type == OperationType.LOAD]
                if load_ops:
                    deps.add(load_ops[-1].op_id)
            
            # Generally, each operation depends on the previous one
            if i > 0:
                prev_op = operations[i-1]
                if not op.op_type in [OperationType.LOAD]:  # LOAD operations can be parallel
                    deps.add(prev_op.op_id)
            
            dependency_graph[op.op_id] = deps
        
        return dependency_graph
    
    def _estimate_costs(self, operations: List[Operation]):
        """Estimate costs for operations"""
        for op in operations:
            # Base cost already set in _create_operation_from_stage
            # Could add more sophisticated cost estimation here
            pass
    
    def _create_execution_stages(self, operations: List[Operation], dependency_graph: Dict[str, Set[str]]) -> List[ExecutionStage]:
        """Create execution stages with parallelization opportunities"""
        stages = []
        remaining_ops = set(op.op_id for op in operations)
        executed_ops = set()
        stage_num = 1
        
        # Group operations into execution stages
        while remaining_ops:
            stage_ops = []
            
            # Find operations that can be executed (dependencies satisfied)
            for op in operations:
                if op.op_id in remaining_ops:
                    deps = dependency_graph.get(op.op_id, set())
                    if deps.issubset(executed_ops):
                        stage_ops.append(op)
            
            # Determine execution mode for this stage
            if len(stage_ops) > 1 and all(op.can_parallelize for op in stage_ops):
                execution_mode = ExecutionMode.PARALLEL
            elif len(stage_ops) > 1:
                execution_mode = ExecutionMode.HYBRID
            else:
                execution_mode = ExecutionMode.SEQUENTIAL
            
            # Create stage
            if stage_ops:
                stage = ExecutionStage(
                    stage_id=f"stage_{stage_num}",
                    operations=stage_ops,
                    execution_mode=execution_mode,
                    max_parallel_ops=self.max_parallel_ops,
                    estimated_duration=max(op.estimated_duration for op in stage_ops) if stage_ops else 0.0
                )
                stages.append(stage)
                
                # Mark ops as executed
                for op in stage_ops:
                    executed_ops.add(op.op_id)
                    remaining_ops.remove(op.op_id)
                
                stage_num += 1
            else:
                # No operations can be executed (circular dependency?)
                break
        
        return stages
    
    def analyze_parallelization_opportunities(self, plan: ExecutionPlan) -> Dict[str, Any]:
        """
        Analyze parallelization opportunities in the plan
        
        Returns:
            Analysis report with recommendations
        """
        total_ops = sum(len(stage.operations) for stage in plan.stages)
        parallel_ops = sum(len(stage.operations) for stage in plan.stages if stage.execution_mode == ExecutionMode.PARALLEL)
        sequential_ops = sum(len(stage.operations) for stage in plan.stages if stage.execution_mode == ExecutionMode.SEQUENTIAL)
        
        # Estimate time savings from parallelization
        sequential_time = sum(op.estimated_duration for stage in plan.stages for op in stage.operations)
        parallel_time = plan.total_estimated_duration
        
        time_saved = sequential_time - parallel_time
        speedup_factor = sequential_time / parallel_time if parallel_time > 0 else 1.0
        
        return {
            'total_operations': total_ops,
            'parallel_operations': parallel_ops,
            'sequential_operations': sequential_ops,
            'time_saved_seconds': time_saved,
            'speedup_factor': round(speedup_factor, 2),
            'parallelization_efficiency': f"{speedup_factor:.1f}x"
        }


# Example usage:
# planner = AIQLPlanner(max_parallel_ops=4)
# plan = planner.create_plan(validated_ast)
# analysis = planner.analyze_parallelization_opportunities(plan)











