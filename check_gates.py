"""
Quality Gate Verification Interface (OFF-002)
Exposes check_all_gates delegating to audit_quality.
"""
from audit_quality import check_all_gates, QualityAuditor, run_quality_audit

__all__ = ["check_all_gates", "QualityAuditor", "run_quality_audit"]

if __name__ == "__main__":
    import sys
    results = check_all_gates()
    sys.exit(0 if results.get("all_passed") else 1)

