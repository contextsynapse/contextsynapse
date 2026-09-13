"""
Unit tests for PII detection and auto-tagging in the ingest pipeline.
"""

import pytest
from contextcore.security.data_security import PIIDetector


class TestPIIDetector:

    def test_detect_email(self):
        result = PIIDetector.detect_pii("Contact us at alice@example.com for info")
        assert "emails" in result
        assert "alice@example.com" in result["emails"]

    def test_detect_phone(self):
        result = PIIDetector.detect_pii("Call me at 555-123-4567")
        assert "phones" in result

    def test_detect_ssn(self):
        result = PIIDetector.detect_pii("SSN is 123-45-6789")
        assert "ssns" in result
        assert "123-45-6789" in result["ssns"]

    def test_detect_credit_card(self):
        result = PIIDetector.detect_pii("Card: 4111-1111-1111-1111")
        assert "credit_cards" in result

    def test_no_pii(self):
        result = PIIDetector.detect_pii("The quick brown fox jumps over the lazy dog")
        assert result == {}

    def test_mask_pii(self):
        text = "Email: alice@example.com, SSN: 123-45-6789"
        masked = PIIDetector.mask_pii(text)
        assert "alice@example.com" not in masked
        assert "123-45-6789" not in masked
        assert "***" in masked

    def test_multiple_pii_types(self):
        text = "alice@example.com called from 555-123-4567 ssn 123-45-6789"
        result = PIIDetector.detect_pii(text)
        assert "emails" in result
        assert "phones" in result
        assert "ssns" in result
