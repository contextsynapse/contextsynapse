#!/usr/bin/env python3
"""
Evaluation Metrics for Regression Tests
Provides metrics for evaluating RAG and search quality
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import math

@dataclass
class EvaluationMetrics:
    """Evaluation metrics for test results."""
    precision_at_k: Optional[float] = None
    recall_at_k: Optional[float] = None
    ndcg_at_k: Optional[float] = None
    mrr: Optional[float] = None  # Mean Reciprocal Rank
    f1_score: Optional[float] = None
    latency_ms: Optional[float] = None
    relevance_score: Optional[float] = None  # Average relevance score
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'precision_at_k': self.precision_at_k,
            'recall_at_k': self.recall_at_k,
            'ndcg_at_k': self.ndcg_at_k,
            'mrr': self.mrr,
            'f1_score': self.f1_score,
            'latency_ms': self.latency_ms,
            'relevance_score': self.relevance_score
        }

class RegressionEvaluator:
    """Evaluates regression test results with metrics."""
    
    def __init__(self, k_values: List[int] = None):
        """
        Initialize evaluator.
        
        Args:
            k_values: List of k values for precision@k, recall@k, NDCG@k
        """
        self.k_values = k_values or [1, 5, 10, 20]
    
    def evaluate_search_result(
        self,
        result: Dict[str, Any],
        expected_relevant: List[str] = None,
        k: int = 10
    ) -> EvaluationMetrics:
        """
        Evaluate a search result.
        
        Args:
            result: Search result dictionary
            expected_relevant: List of expected relevant node IDs
            k: Value of k for metrics
            
        Returns:
            EvaluationMetrics object
        """
        nodes = result.get('nodes', [])
        edges = result.get('edges', [])
        
        # Extract node IDs from results
        retrieved_ids = []
        relevance_scores = []
        
        for node in nodes:
            node_id = node.get('id') or node.get('_id')
            if node_id:
                retrieved_ids.append(str(node_id))
            
            # Extract relevance score if available
            score = node.get('score') or node.get('relevance') or node.get('similarity')
            if score is not None:
                relevance_scores.append(float(score))
        
        # Calculate metrics
        metrics = EvaluationMetrics()
        
        if expected_relevant:
            expected_set = set(str(id) for id in expected_relevant)
            retrieved_set = set(retrieved_ids[:k])
            
            # Precision@k
            if len(retrieved_set) > 0:
                metrics.precision_at_k = len(retrieved_set & expected_set) / len(retrieved_set)
            else:
                metrics.precision_at_k = 0.0
            
            # Recall@k
            if len(expected_set) > 0:
                metrics.recall_at_k = len(retrieved_set & expected_set) / len(expected_set)
            else:
                metrics.recall_at_k = 0.0
            
            # F1 Score
            if metrics.precision_at_k and metrics.recall_at_k:
                if metrics.precision_at_k + metrics.recall_at_k > 0:
                    metrics.f1_score = (2 * metrics.precision_at_k * metrics.recall_at_k) / \
                                     (metrics.precision_at_k + metrics.recall_at_k)
            
            # MRR (Mean Reciprocal Rank)
            for i, node_id in enumerate(retrieved_ids[:k], 1):
                if node_id in expected_set:
                    metrics.mrr = 1.0 / i
                    break
            
            # NDCG@k
            metrics.ndcg_at_k = self._calculate_ndcg(retrieved_ids[:k], expected_set, k)
        
        # Average relevance score
        if relevance_scores:
            metrics.relevance_score = sum(relevance_scores) / len(relevance_scores)
        
        # Latency (if available)
        latency = result.get('query_time_ms') or result.get('execution_time_ms')
        if latency:
            metrics.latency_ms = float(latency)
        
        return metrics
    
    def _calculate_ndcg(
        self,
        retrieved_ids: List[str],
        expected_set: set,
        k: int
    ) -> float:
        """Calculate NDCG@k."""
        if not retrieved_ids or not expected_set:
            return 0.0
        
        # Calculate DCG
        dcg = 0.0
        for i, node_id in enumerate(retrieved_ids[:k], 1):
            if node_id in expected_set:
                relevance = 1.0  # Binary relevance
                dcg += relevance / math.log2(i + 1)
        
        # Calculate IDCG (ideal DCG)
        num_relevant = min(len(expected_set), k)
        idcg = sum(1.0 / math.log2(i + 1) for i in range(1, num_relevant + 1))
        
        if idcg == 0:
            return 0.0
        
        return dcg / idcg
    
    def evaluate_rag_result(
        self,
        result: Dict[str, Any],
        expected_answer_keywords: List[str] = None
    ) -> EvaluationMetrics:
        """
        Evaluate a RAG result.
        
        Args:
            result: RAG result dictionary
            expected_answer_keywords: List of expected keywords in answer
            
        Returns:
            EvaluationMetrics object
        """
        metrics = EvaluationMetrics()
        
        # Extract answer text
        answer = result.get('answer') or result.get('response') or result.get('data', {}).get('answer', '')
        answer_lower = answer.lower() if answer else ''
        
        # Check if expected keywords are present
        if expected_answer_keywords:
            keywords_found = sum(1 for kw in expected_answer_keywords if kw.lower() in answer_lower)
            metrics.precision_at_k = keywords_found / len(expected_answer_keywords) if expected_answer_keywords else 0.0
        
        # Latency
        latency = result.get('query_time_ms') or result.get('execution_time_ms')
        if latency:
            metrics.latency_ms = float(latency)
        
        # Relevance score (if available)
        relevance = result.get('relevance_score') or result.get('confidence')
        if relevance:
            metrics.relevance_score = float(relevance)
        
        return metrics
    
    def evaluate_batch(
        self,
        results: List[Dict[str, Any]],
        expected_relevant_map: Dict[str, List[str]] = None
    ) -> Dict[str, float]:
        """
        Evaluate a batch of results.
        
        Args:
            results: List of result dictionaries
            expected_relevant_map: Map of test_id to expected relevant IDs
            
        Returns:
            Dictionary of average metrics
        """
        all_metrics = []
        
        for result in results:
            test_id = result.get('test_id', '')
            expected_relevant = expected_relevant_map.get(test_id) if expected_relevant_map else None
            
            metrics = self.evaluate_search_result(result, expected_relevant)
            all_metrics.append(metrics)
        
        # Calculate averages
        avg_metrics = {}
        if all_metrics:
            avg_metrics = {
                'avg_precision_at_k': sum(m.precision_at_k or 0 for m in all_metrics) / len(all_metrics),
                'avg_recall_at_k': sum(m.recall_at_k or 0 for m in all_metrics) / len(all_metrics),
                'avg_ndcg_at_k': sum(m.ndcg_at_k or 0 for m in all_metrics) / len(all_metrics),
                'avg_mrr': sum(m.mrr or 0 for m in all_metrics) / len(all_metrics),
                'avg_f1_score': sum(m.f1_score or 0 for m in all_metrics) / len(all_metrics),
                'avg_latency_ms': sum(m.latency_ms or 0 for m in all_metrics) / len(all_metrics),
                'avg_relevance_score': sum(m.relevance_score or 0 for m in all_metrics) / len(all_metrics)
            }
        
        return avg_metrics


