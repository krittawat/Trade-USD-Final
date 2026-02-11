Deployment, Structure, and Migration Protocol
This document establishes the unified standard for system architecture, directory organization, and account migration within the Gold Risk Engine ecosystem.

1. Directory Hierarchy Standards
To manage the high volume of utility scripts (200+ total), the following categorization logic is enforced:

Category	Directory Path	Pattern Match
Debug Tools	backend/scripts/debug/	debug_*.py, check_*.py, diagnose_*.py
Verification	backend/scripts/verify/	verify_*.py, prove_*.py, validate_*.py
Simulations	backend/scripts/simulate/	simulate_*.py, calculate_*.py, backtest_*.py
Execution/Trade	backend/scripts/deploy/	execute_*.py, deploy_*.py, force_*.py, set_*.py
AI Learning	backend/scripts/training/	learn_*.py, train_*.py, evolve_*.py
Legacy/Archive	backend/archive_legacy/	Outdated core logic or retired strategies.
Cent/USC Archive	archive_cent/	All USC-specific symbols and root-level utility scripts.
2. USC to USD Migration Guide
Architectural shift from Cent (USC) to Standard (USD) accounts:

2.1. Parameter Normalization
GLOBAL_DAILY_TARGET_USD: Transitioned from 1800.0 (Cent) to 18.0 (Standard).
DEFAULT_HEDGE_THRESHOLD_USD: Transitioned from 200 to 2.0.
Multiplier: get_account_unit_multiplier() is hardcoded to 1.0 to bypass Cent server detection.
2.2. Logic Updates
Removed / 100 divisors in bot_manager.py and risk_manager.py.
Renamed variables (e.g., daily_target_usc → daily_target_usd).
HOPE MODE targets reset to standard USD values (e.g., $20.00).
3. Structural Integrity Audit and Remediation (February 2026)
3.1. Baseline Audit (Pre-Cleanup)
Status: Non-Compliant Drift
Findings: backend/ root contained 53 files; backend/scripts/ root contained 100 uncategorized scripts.
3.2. Remediation Outcome (Executed Feb 5, 2026)
Action: Massive script categorization via PowerShell automation.
Successes:
backend/ Root: Reduced from 53 to 23 files (75% of non-core scripts isolated).
backend/scripts/ Root: Reduced from 100 to 50 files (50% categorized into sub-folders).
Isolation: Cent-specific assets (analyze_c_symbols.py) successfully moved to archive_cent/.
Remaining Debt: 50 scripts in backend/scripts/ still require manual logic-mapping to sub-folders, but "Level 1" (Production Root) cleanliness is substantially improved.
3.3. Core Production Set
Only the following high-priority production binaries should remain in backend/ root:

master_loop.py (Core execution loop)
run_bot.py (Bot process launcher)
run.py (API entry point)
settings.json (Primary configuration)
Strategy-specific JSON patterns (grid_state.json, etc.)
3.4. Post-Remediation Verification
Verified (Feb 5, 2026): System successfully resumed via start_system.bat on Standard USD account targets ($36.00 daily goal). All categorized scripts in debug/ and deploy/ are fully accessible.
4. Maintenance Rule
All new internal tools must be placed in their respective scripts/ subdirectory rather than the root backend/ directory to maintain "Level 1" cleanliness. Use backend/scripts/cleanup.bat to force-kill zombie processes before redeployment.