"""
AIContextDB Data Security System
Comprehensive security including encryption, access control, PII detection, and audit logging.
"""

import hashlib
import json
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)

class AccessLevel(Enum):
    """Access levels for RBAC."""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"

@dataclass
class AccessControl:
    """Access control entry."""
    user_id: str
    resource: str
    access_level: AccessLevel
    granted_at: datetime
    expires_at: Optional[datetime] = None

class PIIDetector:
    """PII (Personally Identifiable Information) detector."""
    
    # Patterns for common PII
    EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    PHONE_PATTERN = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
    SSN_PATTERN = r'\b\d{3}-\d{2}-\d{4}\b'
    CREDIT_CARD_PATTERN = r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'
    
    @staticmethod
    def detect_pii(text: str) -> Dict[str, List[str]]:
        """Detect PII in text."""
        pii_found = {
            'emails': re.findall(PIIDetector.EMAIL_PATTERN, text),
            'phones': re.findall(PIIDetector.PHONE_PATTERN, text),
            'ssns': re.findall(PIIDetector.SSN_PATTERN, text),
            'credit_cards': re.findall(PIIDetector.CREDIT_CARD_PATTERN, text)
        }
        
        # Remove empty lists
        return {k: v for k, v in pii_found.items() if v}
    
    @staticmethod
    def mask_pii(text: str, mask_char: str = '*') -> str:
        """Mask PII in text."""
        masked = text
        
        # Mask emails
        masked = re.sub(PIIDetector.EMAIL_PATTERN, lambda m: mask_char * len(m.group()), masked)
        
        # Mask phones
        masked = re.sub(PIIDetector.PHONE_PATTERN, lambda m: mask_char * len(m.group()), masked)
        
        # Mask SSNs
        masked = re.sub(PIIDetector.SSN_PATTERN, lambda m: mask_char * len(m.group()), masked)
        
        # Mask credit cards
        masked = re.sub(PIIDetector.CREDIT_CARD_PATTERN, lambda m: mask_char * len(m.group()), masked)
        
        return masked

class DataSecurity:
    """Comprehensive data security system."""
    
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.access_controls: Dict[str, List[AccessControl]] = defaultdict(list)
        self.audit_log: List[Dict[str, Any]] = []
        self.pii_detector = PIIDetector()
        self.encryption_enabled = False
        
        logger.info(f"[EMOJI] DataSecurity initialized for namespace: {namespace}")
    
    def check_access(self, user_id: str, resource: str, required_level: AccessLevel) -> bool:
        """Check if user has required access."""
        if resource not in self.access_controls:
            return False  # No access by default
        
        for acl in self.access_controls[resource]:
            if acl.user_id == user_id:
                # Check expiration
                if acl.expires_at and datetime.now() > acl.expires_at:
                    continue
                
                # Check access level
                if self._has_sufficient_level(acl.access_level, required_level):
                    return True
        
        return False
    
    def _has_sufficient_level(self, granted: AccessLevel, required: AccessLevel) -> bool:
        """Check if granted level is sufficient."""
        level_hierarchy = {
            AccessLevel.READ: 1,
            AccessLevel.WRITE: 2,
            AccessLevel.DELETE: 3,
            AccessLevel.ADMIN: 4
        }
        
        return level_hierarchy.get(granted, 0) >= level_hierarchy.get(required, 0)
    
    def grant_access(self, user_id: str, resource: str, access_level: AccessLevel, 
                    expires_at: Optional[datetime] = None):
        """Grant access to resource."""
        acl = AccessControl(
            user_id=user_id,
            resource=resource,
            access_level=access_level,
            granted_at=datetime.now(),
            expires_at=expires_at
        )
        
        self.access_controls[resource].append(acl)
        self._audit_log('GRANT_ACCESS', user_id, resource, {'access_level': access_level.value})
    
    def revoke_access(self, user_id: str, resource: str):
        """Revoke access to resource."""
        if resource in self.access_controls:
            self.access_controls[resource] = [
                acl for acl in self.access_controls[resource] if acl.user_id != user_id
            ]
            self._audit_log('REVOKE_ACCESS', user_id, resource)
    
    def detect_and_mask_pii(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Detect and mask PII in data."""
        masked_data = data.copy()
        pii_detected = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                detected = self.pii_detector.detect_pii(value)
                if detected:
                    pii_detected[key] = detected
                    masked_data[key] = self.pii_detector.mask_pii(value)
            elif isinstance(value, dict):
                masked_data[key], nested_pii = self._detect_and_mask_nested(value)
                if nested_pii:
                    pii_detected[key] = nested_pii
        
        if pii_detected:
            self._audit_log('PII_DETECTED', None, None, {'pii_types': list(pii_detected.keys())})
        
        return masked_data, pii_detected
    
    def _detect_and_mask_nested(self, data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Recursively detect and mask PII in nested structures."""
        masked = {}
        pii_detected = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                detected = self.pii_detector.detect_pii(value)
                if detected:
                    pii_detected[key] = detected
                    masked[key] = self.pii_detector.mask_pii(value)
                else:
                    masked[key] = value
            elif isinstance(value, dict):
                nested_masked, nested_pii = self._detect_and_mask_nested(value)
                masked[key] = nested_masked
                if nested_pii:
                    pii_detected[key] = nested_pii
            else:
                masked[key] = value
        
        return masked, pii_detected
    
    def _audit_log(self, action: str, user_id: Optional[str], resource: Optional[str], 
                   details: Dict[str, Any] = None):
        """Log security event."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'user_id': user_id,
            'resource': resource,
            'details': details or {}
        }
        
        self.audit_log.append(log_entry)
        
        # Keep only last 10000 entries
        if len(self.audit_log) > 10000:
            self.audit_log = self.audit_log[-10000:]
    
    def get_audit_log(self, user_id: str = None, resource: str = None, 
                     start_time: datetime = None, end_time: datetime = None) -> List[Dict[str, Any]]:
        """Get audit log with filters."""
        filtered = self.audit_log
        
        if user_id:
            filtered = [entry for entry in filtered if entry.get('user_id') == user_id]
        
        if resource:
            filtered = [entry for entry in filtered if entry.get('resource') == resource]
        
        if start_time:
            filtered = [entry for entry in filtered 
                       if datetime.fromisoformat(entry['timestamp']) >= start_time]
        
        if end_time:
            filtered = [entry for entry in filtered 
                       if datetime.fromisoformat(entry['timestamp']) <= end_time]
        
        return filtered

