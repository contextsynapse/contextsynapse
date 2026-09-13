"""Tests for security features: PII detection, auto-tagging, encryption, audit trail."""

import pytest
from contextcore.security.pii import PIIDetector, PIIType, RedactionMode
from contextcore.security.auto_tagger import AutoTagger, ClassificationResult
from contextcore.security.encryption import FieldEncryptor
from contextcore.security.audit_trail import AuditTrail
import tempfile, os


# ═══════════════════════════════════════════════
# PII Detection
# ═══════════════════════════════════════════════

class TestPIIDetection:

    def setup_method(self):
        self.d = PIIDetector()

    def test_detect_email(self):
        r = self.d.scan("Contact me at john.doe@example.com for details")
        assert r.pii_found
        assert any(t == "email" for _, t in r.entities)

    def test_detect_phone(self):
        r = self.d.scan("Call us at +91-9876-543-210 today")
        assert r.pii_found
        assert any(t == "phone" for _, t in r.entities)

    def test_detect_ssn(self):
        r = self.d.scan("SSN: 123-45-6789")
        assert r.pii_found
        assert r.sensitivity == "restricted"

    def test_detect_credit_card(self):
        r = self.d.scan("Card: 4111-1111-1111-1111")
        assert r.pii_found
        assert r.sensitivity == "restricted"

    def test_detect_pan(self):
        r = self.d.scan("PAN: ABCDE1234F")
        assert r.pii_found
        assert any(t == "pan" for _, t in r.entities)

    def test_no_pii(self):
        r = self.d.scan("The weather in Delhi is 35 degrees today")
        assert not r.pii_found
        assert r.sensitivity == "public"

    def test_multiple_pii(self):
        r = self.d.scan("Email: a@b.com, Phone: 555-123-4567, SSN: 111-22-3333")
        assert r.entity_count >= 3

    def test_auto_sensitivity_email(self):
        r = self.d.scan("Contact admin@company.com")
        assert r.sensitivity == "confidential"

    def test_auto_sensitivity_ssn(self):
        r = self.d.scan("SSN 123-45-6789")
        assert r.sensitivity == "restricted"


class TestPIIMasking:

    def setup_method(self):
        self.d = PIIDetector()

    def test_redact_mode(self):
        result = self.d.mask("Email: john@example.com", mode=RedactionMode.REDACT)
        assert "[EMAIL REDACTED]" in result
        assert "john@example.com" not in result

    def test_mask_mode_email(self):
        result = self.d.mask("Email: john@example.com", mode=RedactionMode.MASK)
        assert "john@example.com" not in result
        assert "@example.com" in result  # domain preserved

    def test_hash_mode(self):
        result = self.d.mask("SSN: 123-45-6789", mode=RedactionMode.HASH)
        assert "[SHA:" in result
        assert "123-45-6789" not in result

    def test_allow_mode(self):
        original = "Email: john@example.com"
        result = self.d.mask(original, mode=RedactionMode.ALLOW)
        assert result == original

    def test_mask_node_properties(self):
        props = {"name": "John", "content": "Contact john@x.com for details", "status": "active"}
        masked = self.d.mask_node_properties(props, mode=RedactionMode.REDACT)
        assert "john@x.com" not in masked["content"]
        assert masked["name"] == "John"  # name field might not have PII
        assert masked["status"] == "active"  # non-text field untouched


# ═══════════════════════════════════════════════
# Auto-Tagging
# ═══════════════════════════════════════════════

class TestAutoTagger:

    def setup_method(self):
        self.t = AutoTagger()

    def test_public_content(self):
        r = self.t.classify_text("The weather in Mumbai is sunny today")
        assert r.sensitivity == "public"
        assert not r.pii_detected

    def test_confidential_from_pii(self):
        r = self.t.classify_text("Contact admin@company.com for access")
        assert r.sensitivity == "confidential"
        assert r.pii_detected

    def test_restricted_from_keywords(self):
        r = self.t.classify_text("Board meeting notes: acquisition target is CompanyX")
        assert r.sensitivity == "restricted"
        assert "classified" in r.tags

    def test_internal_from_keywords(self):
        r = self.t.classify_text("INTERNAL ONLY: draft roadmap for Q3")
        assert r.sensitivity == "internal"

    def test_confidential_from_financial(self):
        r = self.t.classify_text("Employee salary: $120,000 per year")
        assert r.sensitivity == "confidential"

    def test_tag_node(self):
        props = {"name": "Report", "content": "Contact admin@x.com for the salary details"}
        tagged = self.t.tag_node(props)
        assert tagged["sensitivity"] in ("confidential", "restricted")
        assert tagged["pii_detected"] is True
        assert tagged["_auto_classified"] is True
        assert any("pii:" in t for t in tagged["tags"])

    def test_classify_result_to_dict(self):
        r = self.t.classify_text("SSN 123-45-6789")
        d = r.to_dict()
        assert d["pii_detected"] is True
        assert d["sensitivity"] == "restricted"
        assert any("pii:ssn" in t for t in d["tags"])


# ═══════════════════════════════════════════════
# Field Encryption
# ═══════════════════════════════════════════════

class TestFieldEncryption:

    def test_encrypt_decrypt_roundtrip(self):
        enc = FieldEncryptor(master_key="test-key-for-encryption")
        original = "This is sensitive data"
        encrypted = enc.encrypt_value(original)
        assert encrypted != original
        assert encrypted.startswith("ENC:")
        decrypted = enc.decrypt_value(encrypted)
        assert decrypted == original

    def test_encrypt_properties(self):
        enc = FieldEncryptor(master_key="test-key")
        props = {"name": "Public Name", "content": "Secret content", "status": "active"}
        encrypted = enc.encrypt_properties(props, fields=["content"])
        assert encrypted["name"] == "Public Name"  # not encrypted
        assert encrypted["content"].startswith("ENC:")  # encrypted
        assert encrypted["status"] == "active"  # not encrypted
        assert "content" in encrypted.get("_encrypted_fields", [])

    def test_decrypt_properties(self):
        enc = FieldEncryptor(master_key="test-key")
        props = {"content": "Secret", "status": "active"}
        encrypted = enc.encrypt_properties(props, fields=["content"])
        decrypted = enc.decrypt_properties(encrypted)
        assert decrypted["content"] == "Secret"
        assert "_encrypted_fields" not in decrypted

    def test_searchable_token_deterministic(self):
        enc = FieldEncryptor(master_key="test-key")
        t1 = enc.searchable_token("hello world")
        t2 = enc.searchable_token("hello world")
        assert t1 == t2
        assert t1.startswith("STOK:")

    def test_searchable_token_different_for_different_input(self):
        enc = FieldEncryptor(master_key="test-key")
        t1 = enc.searchable_token("hello")
        t2 = enc.searchable_token("world")
        assert t1 != t2

    def test_no_key_passthrough(self):
        enc = FieldEncryptor(master_key="")
        props = {"content": "plaintext"}
        result = enc.encrypt_properties(props)
        assert result["content"] == "plaintext"

    def test_available_flag(self):
        enc = FieldEncryptor(master_key="test-key")
        # available depends on whether cryptography package is installed
        assert isinstance(enc.available, bool)


# ═══════════════════════════════════════════════
# Audit Trail
# ═══════════════════════════════════════════════

class TestAuditTrail:

    @pytest.fixture
    def trail(self, tmp_path):
        db_path = str(tmp_path / "audit_test.db")
        return AuditTrail(db_path=db_path)

    def test_log_and_query(self, trail):
        entry_id = trail.log("user:admin", "delete_context", "delete",
                             resource_id="ctx_123", namespace="ns1")
        assert entry_id > 0
        entries = trail.query(actor="user:admin")
        assert len(entries) == 1
        assert entries[0]["action"] == "delete_context"

    def test_immutable_append_only(self, trail):
        trail.log("agent:a1", "search_nodes", "read")
        trail.log("agent:a1", "add_knowledge", "write")
        assert trail.count() == 2

    def test_hash_chain_integrity(self, trail):
        trail.log("user:1", "action1", "read")
        trail.log("user:2", "action2", "write")
        trail.log("user:3", "action3", "delete")
        result = trail.verify_integrity()
        assert result["valid"] is True
        assert result["entries_checked"] == 3

    def test_query_filters(self, trail):
        trail.log("agent:a1", "search_nodes", "read", namespace="ns1")
        trail.log("agent:a2", "add_knowledge", "write", namespace="ns2")
        trail.log("user:admin", "delete_context", "delete", namespace="ns1")

        r1 = trail.query(actor="agent:a1")
        assert len(r1) == 1

        r2 = trail.query(operation_type="read")
        assert len(r2) == 1

        r3 = trail.query(namespace="ns1")
        assert len(r3) == 2

    def test_count(self, trail):
        trail.log("a", "x", "read")
        trail.log("b", "y", "write")
        assert trail.count() == 2
        assert trail.count(actor="a") == 1

    def test_export_csv(self, trail):
        trail.log("user:1", "login", "read", ip_address="10.0.0.1")
        trail.log("agent:a1", "search", "read")
        csv_content = trail.export_csv()
        assert "user:1" in csv_content
        assert "login" in csv_content
        assert "10.0.0.1" in csv_content

    def test_details_stored(self, trail):
        trail.log("agent:a1", "search_nodes", "read",
                   details={"query": "election", "results": 15})
        entries = trail.query(actor="agent:a1")
        import json
        details = json.loads(entries[0]["details"])
        assert details["query"] == "election"
        assert details["results"] == 15

    def test_failed_operations_logged(self, trail):
        trail.log("agent:a1", "delete_context", "delete", success=False,
                   details={"error": "Permission denied"})
        entries = trail.query(action="delete_context")
        assert entries[0]["success"] == 0
